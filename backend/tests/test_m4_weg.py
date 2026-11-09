"""M4 WEG tests -- persistence, RLS, immutability, atomic finalize, and the M4
exit (Wirtschaftsplan + WEG-Jahresabrechnung with hand-checked Abrechnungsspitze
+ Vermoegensbericht, all as PDF; amounts consistent).

Uses the same fixtures as M0-M3. The engine math itself is proven in
tests/engine/ (Golden + property-based); here we prove the DB invariants and the
full create->compute->finalize->PDF path through the real service + API.
"""

from __future__ import annotations

import os
import psycopg2
import uuid
from datetime import date
from decimal import Decimal

from tests.financial_coverage_helpers import attest_fixture, finalize_weg_fixture, weg_values, reserve_evidence
import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.mark.asyncio
@pytest.mark.parametrize('day', [0, 29, 30, 31])
async def test_new_plan_refuses_due_days_outside_one_through_twenty_eight(client_a_admin, m4_seed, day):
    response = await client_a_admin.post('/api/economic-plans', json={
        'property_id': m4_seed['property'], 'plan_year': 2026, 'monthly_due_day': day})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('due_input', [{}, {'monthly_due_day': None}])
async def test_new_plan_defaults_an_omitted_or_null_due_day_to_one(client_a_admin, m4_seed, due_input):
    response = await client_a_admin.post('/api/economic-plans', json={
        'property_id': m4_seed['property'], 'plan_year': 2026, **due_input})
    assert response.status_code == 201, response.text
    assert response.json()['monthly_due_day'] == 1


@pytest.mark.parametrize('day', range(1, 29))
def test_new_plan_accepts_each_resolved_due_day(day):
    from app.api.economic_plans import EconomicPlanCreate

    assert EconomicPlanCreate(property_id=str(uuid.uuid4()), plan_year=2026,
        monthly_due_day=day).monthly_due_day == day


@pytest.mark.parametrize('day', [0, 29, 30, 31])
def test_plan_request_refuses_due_days_outside_one_through_twenty_eight(day):
    from pydantic import ValidationError
    from app.api.economic_plans import EconomicPlanCreate

    with pytest.raises(ValidationError):
        EconomicPlanCreate(property_id=str(uuid.uuid4()), plan_year=2026, monthly_due_day=day)


@pytest.mark.asyncio
async def test_historical_null_plan_keeps_its_frozen_payload_and_pdf_while_defaulting_to_first(
    client_a_admin, m4_seed,
):
    # NULL is the original API default and remains a valid frozen historical value.
    plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed, legacy_due_day=None)
    before = (await client_a_admin.get(f'/api/economic-plans/{plan_id}')).json()
    pdf_before = (await client_a_admin.get(f'/api/economic-plans/{plan_id}/pdf')).content
    with psycopg2.connect(SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT monthly_due_day, snapshot_payload, snapshot_sha256 FROM economic_plan WHERE id=%s', (plan_id,))
        frozen_before = cursor.fetchone()
    result = await client_a_admin.get(f"/api/payments/mietkonto/owner/{m4_seed['owner_a']}", params={
        'economic_plan_id': plan_id, 'period_start': '2025-03-01', 'period_end': '2025-03-31',
        'as_of': '2025-03-02'})
    assert result.status_code == 200, result.text
    assert result.json()['open_items'][0]['due_date'] == '2025-03-03'
    assert result.json()['due_balance'] == '0.00'
    assert (await client_a_admin.get(f'/api/economic-plans/{plan_id}')).json() == before
    assert (await client_a_admin.get(f'/api/economic-plans/{plan_id}/pdf')).content == pdf_before
    with psycopg2.connect(SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT monthly_due_day, snapshot_payload, snapshot_sha256 FROM economic_plan WHERE id=%s', (plan_id,))
        assert cursor.fetchone() == frozen_before


@pytest.mark.asyncio
@pytest.mark.parametrize('day', [29, 30, 31])
@pytest.mark.parametrize('year,month,last,cutoff,due', [
    (2025, '07', 31, '2025-07-28', None),
    (2025, '02', 28, '2025-02-27', '2025-02-28'),
    (2026, '02', 28, '2026-03-01', '2026-03-02'),
])
async def test_historical_explicit_due_days_remain_frozen_with_the_approved_month_end_rule(
    client_a_admin, m4_seed, day, year, month, last, cutoff, due,
):
    plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed, legacy_due_day=day, plan_year=year)
    before = (await client_a_admin.get(f'/api/economic-plans/{plan_id}')).json()
    pdf_before = (await client_a_admin.get(f'/api/economic-plans/{plan_id}/pdf')).content
    response = await client_a_admin.get(f"/api/payments/mietkonto/owner/{m4_seed['owner_a']}", params={
        'economic_plan_id': plan_id, 'period_start': f'{year}-{month}-01', 'period_end': f'{year}-{month}-{last}',
        'as_of': cutoff})
    assert response.status_code == 200, response.text
    assert response.json()['open_items'][0]['due_date'] == (due or f'{year}-{month}-{min(day, last)}')
    assert response.json()['due_balance'] == '0.00'
    assert (await client_a_admin.get(f'/api/economic-plans/{plan_id}')).json() == before
    assert before['monthly_due_day'] == day
    assert (await client_a_admin.get(f'/api/economic-plans/{plan_id}/pdf')).content == pdf_before


# ===========================================================================
# Seed: a WEG (org_a) -- 2 units MEA 600/400, two owners; cost entries from
# three spheres (umlagefaehig / nicht umlagefaehig / Erhaltungsruecklage).
# Mirrors engine Golden 2/3 so the Abrechnungsspitze is hand-checkable.
# ===========================================================================
def _seed_weg(seed_data):
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "u2": uuid.uuid4(),
        "person_a": uuid.uuid4(),
        "person_b": uuid.uuid4(),
        "owner_a": uuid.uuid4(),
        "owner_b": uuid.uuid4(),
        "bank_giro": uuid.uuid4(),
        "bank_ruecklage": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute("SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,))
            return cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "WEG Hauptstrasse 5", "WEG"),
        )
        # Units: U1 MEA 600, U2 MEA 400.
        for uid, num, mea in [(ids["u1"], "1", 600), (ids["u2"], "2", 400)]:
            cur.execute(
                "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uid), org, str(ids["property"]), num, "WOHNUNG", mea, mea, mea),
            )
        # Two owners, one per unit, whole period.
        for pid, oid, first, uid in [
            (ids["person_a"], ids["owner_a"], "Anton", ids["u1"]),
            (ids["person_b"], ids["owner_b"], "Berta", ids["u2"]),
        ]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(pid), org, first, "Eigentuemer"),
            )
            cur.execute(
                "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(oid), org, str(pid)),
            )
            cur.execute(
                "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), org, str(uid), str(oid), date(2000, 1, 1), None),
            )

        # Bank accounts (model anchor; manual balances captured at report time).
        for bid, atype, label in [
            (ids["bank_giro"], "LAUFEND", "Girokonto"),
            (ids["bank_ruecklage"], "RUECKLAGE", "Ruecklagenkonto"),
        ]:
            cur.execute(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(bid), org, str(ids["property"]), atype, label),
            )

        # Cost entries (active chain) for 2025. Owner sphere reads
        # owner_distribution_key (here MEA for all). Three spheres:
        #   Gartenpflege 4000 UMLAGEFAEHIG ; Verwalter 3000 NICHT_UMLAGEFAEHIG ;
        #   Ruecklagen-Zufuehrung 6000 (ERHALTUNGSRUECKLAGE, reserve_relevant).
        def add_cost(code, gross, owner_key="MEA"):
            ce = uuid.uuid4()
            cur.execute(
                "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
                "invoice_date, service_period_from, service_period_to, gross_amount, owner_distribution_key, "
                "tenant_distribution_key, tenant_apportionable, status, source, confirmed_at, confirmed_by) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
                (
                    str(ce), org, str(ids["property"]), str(cat(code)),
                    date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                    gross, owner_key, "MEA", "NICHT_UMLAGEFAEHIG",
                    str(seed_data["user_a_admin_id"]),
                ),
            )
            return ce

        ids["ce_garten"] = add_cost("GARTENPFLEGE", Decimal("4000.00"))
        ids["ce_verwalter"] = add_cost("WEG_VERWALTERKOSTEN", Decimal("3000.00"))
        ids["ce_ruecklage"] = add_cost("ERHALTUNGSRUECKLAGE", Decimal("6000.00"))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


@pytest.fixture
def m4_seed(seed_data):
    return _seed_weg(seed_data)


@pytest.fixture
def m4_withdrawal_seed(m4_seed):
    # A reserve-classified receipt covers 8,000 without changing owner costs.
    receipt = previous_cost({**m4_seed, 'ce_garten': m4_seed['ce_ruecklage']}, '8000.00')
    return {**m4_seed, 'ce_garten': str(receipt)}


async def _create_and_finalize_plan(client, m4_seed, reserve="6000.00", *, legacy_due_day=..., plan_year=2025):
    """Create + finalize a Wirtschaftsplan with the two owners' MEA shares."""
    resp = await client.post("/api/economic-plans", json={
        "property_id": m4_seed["property"],
        "plan_year": plan_year,
        "period_start": f"{plan_year}-01-01",
        "period_end": f"{plan_year}-12-31",
    })
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["id"]
    if legacy_due_day is not ...:
        with psycopg2.connect(SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('UPDATE economic_plan SET monthly_due_day=%s WHERE id=%s', (legacy_due_day, plan_id))
    # Planned: Gartenpflege 4000 + Verwalter 3000 = 7000 cost; reserve 6000.
    body = {
        "planned_costs": [
            {"category_code": "GARTENPFLEGE", "label": "Gartenpflege", "planned_amount": "4000.00", "cost_group": "UMLAGEFAEHIG"},
            {"category_code": "WEG_VERWALTERKOSTEN", "label": "Verwalter", "planned_amount": "3000.00", "cost_group": "NICHT_UMLAGEFAEHIG"},
        ],
        "reserve_contribution_total": reserve,
    }
    await attest_fixture(client, "economic_plan", plan_id, body)
    resp = await client.post(f"/api/economic-plans/{plan_id}/finalize", json=body)
    assert resp.status_code == 200, resp.text
    return plan_id


@pytest.mark.asyncio
@pytest.mark.parametrize("route, table", [
    ("economic-plans", "economic_plan"),
    ("weg-settlements/asset-reports", "settlement_run"),
    ("weg-settlements", "settlement_run"),
])
@pytest.mark.parametrize("field", ["property_id", "management_mandate_id"])
async def test_foreign_and_unknown_parents_create_no_financial_draft(
    client_a_admin, client_b_admin, m4_seed, migrator_session, field, route, table,
):
    body = {"property_id": m4_seed["property"], "plan_year": 2025,
            "period_start": "2025-01-01", "period_end": "2025-12-31"}
    if route != "economic-plans":
        body.pop("plan_year")
    client = client_b_admin if field == "property_id" else client_a_admin
    foreign = m4_seed["property"] if field == "property_id" else m4_seed["mandate_b_id"]
    if route == "weg-settlements":
        plan = await client_a_admin.post("/api/economic-plans", json={
            "property_id": m4_seed["property"], "plan_year": 2025,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert plan.status_code == 201, plan.text
        body["economic_plan_id"] = plan.json()["id"]
        client = client_a_admin
        if field == "property_id":
            property_response = await client_b_admin.post("/api/properties", json={
                "name": "Foreign WEG parent", "management_type": "WEG",
            })
            assert property_response.status_code == 201, property_response.text
            foreign = property_response.json()["id"]
    before = await migrator_session.scalar(text(f"SELECT count(*) FROM {table}"))
    responses = []
    expected_status = 422 if route == "weg-settlements" and field == "property_id" else 404
    for parent in (foreign, uuid.uuid4()):
        response = await client.post(f"/api/{route}", json=body | {field: str(parent)})
        assert response.status_code == expected_status, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]
    assert await migrator_session.scalar(text(f"SELECT count(*) FROM {table}")) == before


# T54: prior-year actuals are a proposal, never a computation or finalization.
@pytest.fixture
def previous_world(seed_data):
    return _seed_weg(seed_data)


def previous_request(world, **changes):
    return {"property_id": world["property"], "plan_year": 2026, **changes}


async def previous_preview(api, world, **changes):
    response = await api.post("/api/economic-plans/previous/preview", json=previous_request(world, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def previous_adoption(world, preview, **changes):
    return {**previous_request(world), "expected_source_digest": preview["source_digest"],
            "planned_costs": preview["planned_costs"],
            "reserve_contribution_total": preview["reserve_contribution_total"], **changes}


@pytest.mark.asyncio
async def test_previous_preview_exact_categories_reserve_and_no_writes(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    before = (await api.get("/api/economic-plans")).json()
    sources = (await api.get("/api/cost-entries")).json()
    preview = await previous_preview(api, w)
    assert preview["source_year"] == 2025 and preview["plan_year"] == 2026
    assert preview["period_start"] == "2025-01-01" and preview["period_end"] == "2025-12-31"
    rows = {r["category_code"]: r for r in preview["planned_costs"]}
    assert set(rows) == {"GARTENPFLEGE", "WEG_VERWALTERKOSTEN"}
    assert Decimal(rows["GARTENPFLEGE"]["planned_amount"]) == Decimal("4000.00")
    assert rows["GARTENPFLEGE"]["cost_group"] == "UMLAGEFAEHIG"
    assert Decimal(rows["WEG_VERWALTERKOSTEN"]["planned_amount"]) == Decimal("3000.00")
    assert rows["WEG_VERWALTERKOSTEN"]["cost_group"] == "NICHT_UMLAGEFAEHIG"
    assert all(r["category_id"] and r["distribution_key"] == "MEA" for r in rows.values())
    assert Decimal(preview["reserve_contribution_total"]) == Decimal("6000.00")
    assert len(preview["source_digest"]) == 64
    assert (await api.get("/api/economic-plans")).json() == before
    assert (await api.get("/api/cost-entries")).json() == sources


@pytest.mark.asyncio
async def test_previous_create_retains_editable_draft_inputs_and_reload(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    body = previous_adoption(w, preview)
    body["planned_costs"][0]["planned_amount"] = "4123.45"
    body["reserve_contribution_total"] = "6500.00"
    created = await api.post("/api/economic-plans/previous", json=body)
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == "draft" and draft["snapshot_sha256"] is None
    assert draft["plan_year"] == 2026 and draft["period_start"] == "2026-01-01"
    assert draft["period_end"] == "2026-12-31" and not draft["lines"]
    assert draft["draft_inputs"]["planned_costs"] == body["planned_costs"]
    assert Decimal(draft["draft_inputs"]["reserve_contribution_total"]) == Decimal("6500.00")
    assert (await api.get(f"/api/economic-plans/{draft['id']}")).json() == draft
    assert (await api.get(f"/api/economic-plans/{draft['id']}/pdf")).status_code == 409


@pytest.mark.asyncio
async def test_previous_stale_basis_creates_no_draft(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=previous_adoption(
        w, preview, expected_source_digest="0" * 64))
    assert response.status_code == 409, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_foreign_property_is_not_disclosed(client_b_admin, previous_world):
    response = await client_b_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world))
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_previous_readonly_cannot_prepare_or_adopt(client_a_readonly, previous_world):
    body = previous_request(previous_world)
    assert (await client_a_readonly.post("/api/economic-plans/previous/preview", json=body)).status_code == 403
    body.update(expected_source_digest="0" * 64, planned_costs=[], reserve_contribution_total="0.00")
    assert (await client_a_readonly.post("/api/economic-plans/previous", json=body)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [0, 1, 10000, 2026.5, "2026"])
async def test_previous_invalid_target_year_rejected(client_a_admin, previous_world, year):
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world, plan_year=year))
    assert response.status_code == 422, response.text


def previous_cost(world, amount, *, status="confirmed", start="2025-01-01", end="2025-12-31", invoice="2025-06-01", category=None):
    import psycopg2
    key = uuid.uuid4()
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO cost_entry(id,organization_id,property_id,cost_category_id,
                cost_category_plan_version,invoice_date,service_period_from,service_period_to,gross_amount,
                owner_distribution_key,tenant_distribution_key,tenant_apportionable,status,source,confirmed_at,confirmed_by)
                SELECT %s,organization_id,property_id,COALESCE(%s::uuid,cost_category_id),cost_category_plan_version,%s,%s,%s,%s,
                'MEA','MEA','NICHT_UMLAGEFAEHIG',%s,'MANUAL',
                CASE WHEN %s='confirmed' THEN now() END,CASE WHEN %s='confirmed' THEN confirmed_by END
                FROM cost_entry WHERE id=%s""", (str(key), str(category) if category else None, invoice, start, end, Decimal(amount), status, status, status, world["ce_garten"]))
    return key


@pytest.mark.asyncio
async def test_previous_current_signed_and_period_prorated_sources(client_a_admin, previous_world):
    import psycopg2
    w = previous_world
    previous_cost(w, "-20.05")
    previous_cost(w, "365.00", start="2024-07-01", end="2025-06-30", invoice="2026-06-01")
    old = previous_cost(w, "111.11")
    replacement = previous_cost(w, "222.22")
    previous_cost(w, "999.00", status="needs_review")
    previous_cost(w, "777.00", start="2024-01-01", end="2024-12-31")
    previous_cost(w, "888.00", start="2026-01-01", end="2026-12-31")
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE cost_entry SET superseded_by_id=%s WHERE id=%s", (str(replacement), str(old)))
    preview = await previous_preview(client_a_admin, w)
    rows = {r["category_code"]: r for r in preview["planned_costs"]}
    assert Decimal(rows["GARTENPFLEGE"]["planned_amount"]) == Decimal("4383.17")
    assert Decimal(rows["WEG_VERWALTERKOSTEN"]["planned_amount"]) == Decimal("3000.00")
    assert Decimal(preview["reserve_contribution_total"]) == Decimal("6000.00")


@pytest.mark.asyncio
async def test_previous_actual_source_change_invalidates_digest(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    old = await previous_preview(api, w)
    before = (await api.get("/api/economic-plans")).json()
    previous_cost(w, "1.00")
    response = await api.post("/api/economic-plans/previous", json=previous_adoption(w, old))
    assert response.status_code == 409, response.text
    assert (await api.get("/api/economic-plans")).json() == before
    fresh = await previous_preview(api, w)
    assert fresh["source_digest"] != old["source_digest"]
    assert Decimal(next(r for r in fresh["planned_costs"] if r["category_code"] == "GARTENPFLEGE")["planned_amount"]) == Decimal("4001.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,expected", [("-4000.00", 200), ("-5000.00", 422)])
async def test_previous_zero_is_preserved_negative_net_is_not_clipped(client_a_admin, previous_world, amount, expected):
    previous_cost(previous_world, amount)
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world))
    assert response.status_code == expected, response.text
    if expected == 200:
        row = next(row for row in response.json()["planned_costs"] if row["category_code"] == "GARTENPFLEGE")
        assert Decimal(row["planned_amount"]) == Decimal("0.00")


@pytest.mark.asyncio
async def test_previous_empty_source_does_not_manufacture_zero_plan(client_a_admin, previous_world):
    before = (await client_a_admin.get("/api/economic-plans")).json()
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world, plan_year=2028))
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_late_failure_rolls_back_new_draft(client_a_admin, previous_world, monkeypatch):
    from fastapi import HTTPException
    from app.repositories.audit_repo import AuditRepository
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    before = (await api.get("/api/economic-plans")).json()
    original = AuditRepository.create
    calls = []

    async def fail_after_insert(self, event):
        if event.entity_type == "economic_plan":
            calls.append(event.entity_id)
            raise HTTPException(422, "Synthetic late plan audit failure")
        return await original(self, event)

    monkeypatch.setattr(AuditRepository, "create", fail_after_insert)
    response = await api.post("/api/economic-plans/previous", json=previous_adoption(w, preview))
    assert response.status_code == 422 and len(calls) == 1, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_edited_values_compute_without_changing_final_original(client_a_admin, previous_world):
    api, w = client_a_admin, previous_world
    old_id = await _create_and_finalize_plan(api, w)
    original = (await api.get(f"/api/economic-plans/{old_id}")).json()
    original_pdf = await api.get(f"/api/economic-plans/{old_id}/pdf")
    assert original_pdf.status_code == 200 and original_pdf.content.startswith(b"%PDF")
    preview = await previous_preview(api, w)
    adopted = previous_adoption(w, preview)
    next(row for row in adopted["planned_costs"] if row["category_code"] == "GARTENPFLEGE")["planned_amount"] = "4100.25"
    adopted["reserve_contribution_total"] = "6500.00"
    response = await api.post("/api/economic-plans/previous", json=adopted)
    assert response.status_code == 201, response.text
    draft = response.json()
    inputs = draft["draft_inputs"]
    assert (await api.post(f"/api/economic-plans/{draft['id']}/compute", json=inputs)).status_code == 422
    # The detail editor can submit more precision than the initial adoption form.
    next(row for row in inputs["planned_costs"] if row["category_code"] == "GARTENPFLEGE")["planned_amount"] = "4100.251"
    await attest_fixture(api, "economic_plan", draft["id"], inputs)
    computed = await api.post(f"/api/economic-plans/{draft['id']}/compute", json=inputs)
    assert computed.status_code == 200, computed.text
    reopened = await api.get(f"/api/economic-plans/{draft['id']}")
    assert reopened.status_code == 200, reopened.text
    for response in (computed, reopened):
        row = next(row for row in response.json()["draft_inputs"]["planned_costs"] if row["category_code"] == "GARTENPFLEGE")
        assert row["planned_amount"] == "4100.251"
    owner_lines = [line for line in computed.json()["lines"] if line["line_type"] == "OWNER_ADVANCE"]
    assert sorted(Decimal(line["planned_amount"]) for line in owner_lines) == [Decimal("5440.10"), Decimal("8160.15")]
    assert (await api.get(f"/api/economic-plans/{old_id}")).json() == original
    assert (await api.get(f"/api/economic-plans/{old_id}/pdf")).content == original_pdf.content


from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: E402,F401


def previous_plan_count(ids):
    import psycopg2
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM economic_plan WHERE organization_id=%s AND property_id=%s",
                           (str(ids["org"]), str(ids["property"])))
            return cursor.fetchone()[0]


async def previous_scoped_fixture(session, seed):
    from sqlalchemy import text
    from tests.test_property_year_report import fixture, cost, ready
    from tests.test_scoped_account_statements import mutate
    ids = await fixture(session, seed, management_type="WEG", scope_type="WEG",
                        start="2025-01-01", end="2025-12-31")
    await cost(session, ids, "100.00")
    await cost(session, ids, "25.00")
    await ready(session, ids)
    ids["target"] = uuid.uuid4()
    await mutate(text("""INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,
        label,valid_from,valid_to,whole_property,origin,created_by)
        VALUES (:target,:org,:property,'WEG','Next year plan','2026-01-01','2026-12-31',true,'REVIEWED',:actor)"""), ids)
    await mutate(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:org,:target,:clerk,ARRAY['read','prepare'],:actor,'Explicit next-year plan preparation')"""), ids)
    return ids


def previous_scoped_request(ids):
    return {"property_id": str(ids["property"]), "plan_year": 2026,
            "source_accounting_scope_id": str(ids["scope"]), "accounting_scope_id": str(ids["target"])}


@pytest.mark.asyncio
async def test_previous_independent_read_and_prepare_year_scopes(migrator_session, scope_seed):
    from tests.test_property_year_report import client
    ids = await previous_scoped_fixture(migrator_session, scope_seed)
    body = previous_scoped_request(ids)
    async with client(ids) as api:
        response = await api.post("/api/economic-plans/previous/preview", json=body)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert len(preview["planned_costs"]) == 1
        assert Decimal(preview["planned_costs"][0]["planned_amount"]) == Decimal("125.00")
        response = await api.post("/api/economic-plans/previous", json={**body,
            "expected_source_digest": preview["source_digest"], "planned_costs": preview["planned_costs"],
            "reserve_contribution_total": "0.00"})
        assert response.status_code == 201, response.text
        assert response.json()["accounting_scope_id"] == str(ids["target"])
        assert (await api.get(f"/api/economic-plans/{response.json()['id']}")).json() == response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", ["scope", "target"])
async def test_previous_revoked_year_authority_creates_nothing(migrator_session, scope_seed, revoked):
    from sqlalchemy import text
    from tests.test_property_year_report import client
    from tests.test_scoped_account_statements import mutate
    ids = await previous_scoped_fixture(migrator_session, scope_seed)
    body = previous_scoped_request(ids)
    async with client(ids) as api:
        projected = await api.post("/api/economic-plans/previous/preview", json=body)
        assert projected.status_code == 200, projected.text
        preview = projected.json()
    await mutate(text("""UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor
        WHERE organization_id=:org AND scope_id=:revoked AND user_id=:clerk AND revoked_at IS NULL"""),
        {**ids, "revoked": ids[revoked]})
    async with client(ids) as api:
        response = await api.post("/api/economic-plans/previous", json={**body,
            "expected_source_digest": preview["source_digest"], "planned_costs": preview["planned_costs"],
            "reserve_contribution_total": "0.00"})
        assert response.status_code == 404, response.text
    async with client(ids, "actor") as api:
        # Admin has no implicit source grant either; use the actual table only in the test fixture below.
        assert (await api.post("/api/economic-plans/previous/preview", json=body)).status_code == 404
    assert previous_plan_count(ids) == 0


@pytest.mark.asyncio
async def test_previous_waits_for_financial_lock(client_a_admin, previous_world, migrator_session):
    import asyncio
    from sqlalchemy import text
    session, w = migrator_session, previous_world
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                          {"key": f"financial-inputs:{w['org_a_id']}"})
    blocker = await session.scalar(text("SELECT pg_backend_pid()"))
    task = asyncio.create_task(client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(w)))
    waiting = False
    try:
        for _ in range(200):
            waiting = await session.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                WHERE l.locktype='advisory' AND NOT l.granted
                AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {"blocker": blocker})
            if waiting or task.done():
                break
            await asyncio.sleep(0.01)
    finally:
        await session.rollback()
    response = await asyncio.wait_for(task, timeout=15)
    assert waiting, "Prior-year proposal must wait for the existing financial-input boundary"
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_previous_parallel_previews_are_equal_and_nonwriting(client_a_admin, previous_world):
    import asyncio
    before = (await client_a_admin.get("/api/economic-plans")).json()
    responses = await asyncio.gather(*(client_a_admin.post("/api/economic-plans/previous/preview",
        json=previous_request(previous_world)) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    assert (await client_a_admin.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["-1.00", "NaN", "1.001", "1000000000000.00"])
async def test_previous_invalid_edited_amount_rolls_back(client_a_admin, previous_world, amount):
    api, w = client_a_admin, previous_world
    preview = await previous_preview(api, w)
    body = previous_adoption(w, preview)
    body["planned_costs"][0]["planned_amount"] = amount
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=body)
    assert response.status_code == 422, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("category_id", "00000000-0000-0000-0000-000000000001"),
    ("category_code", "GRUNDSTEUER"), ("cost_group", "NICHT_UMLAGEFAEHIG"), ("distribution_key", "AREA")])
async def test_previous_forged_category_metadata_cannot_be_adopted(client_a_admin, previous_world, field, value):
    api, w = client_a_admin, previous_world
    projected = await previous_preview(api, w)
    body = previous_adoption(w, projected)
    garden = next(row for row in body["planned_costs"] if row["category_code"] == "GARTENPFLEGE")
    garden[field] = value
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=body)
    assert response.status_code == 422, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["drop", "duplicate"])
async def test_previous_incomplete_or_duplicate_categories_not_silently_changed(client_a_admin, previous_world, change):
    api, w = client_a_admin, previous_world
    projected = await previous_preview(api, w)
    body = previous_adoption(w, projected)
    if change == "drop":
        body["planned_costs"].pop()
    else:
        body["planned_costs"].append(dict(body["planned_costs"][0]))
    before = (await api.get("/api/economic-plans")).json()
    response = await api.post("/api/economic-plans/previous", json=body)
    assert response.status_code == 422, response.text
    assert (await api.get("/api/economic-plans")).json() == before


@pytest.mark.asyncio
async def test_previous_live_role_downgrade_blocks_stored_preview(migrator_session, scope_seed):
    from sqlalchemy import text
    from tests.test_property_year_report import client
    from tests.test_scoped_account_statements import mutate
    ids = await previous_scoped_fixture(migrator_session, scope_seed)
    body = previous_scoped_request(ids)
    async with client(ids) as api:
        response = await api.post("/api/economic-plans/previous/preview", json=body)
        assert response.status_code == 200, response.text
        projected = response.json()
        await mutate(text("UPDATE membership SET role='READ_ONLY' WHERE organization_id=:org AND user_id=:clerk"), ids)
        assert (await api.post("/api/economic-plans/previous/preview", json=body)).status_code == 403
        response = await api.post("/api/economic-plans/previous", json={**body,
            "expected_source_digest": projected["source_digest"], "planned_costs": projected["planned_costs"],
            "reserve_contribution_total": "0.00"})
        assert response.status_code == 403, response.text
    assert previous_plan_count(ids) == 0


@pytest.mark.asyncio
async def test_previous_inactive_category_fails_whole_proposal(client_a_admin, previous_world):
    import psycopg2
    category = uuid.uuid4()
    with psycopg2.connect(SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO cost_category(id,plan_version,code,label,cost_group,
                tenant_apportionability_default,owner_billing_relevant_default,
                owner_distribution_key_default,tenant_distribution_key_default,reserve_relevant,is_active)
                VALUES (%s,1,%s,'T54 inactive category','UMLAGEFAEHIG','UMLAGEFAEHIG',true,'MEA','MEA',false,false)""",
                (str(category), "T54_INACTIVE_" + uuid.uuid4().hex[:12]))
    previous_cost(previous_world, "100.00", category=category)
    before = (await client_a_admin.get("/api/economic-plans")).json()
    response = await client_a_admin.post("/api/economic-plans/previous/preview", json=previous_request(previous_world))
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get("/api/economic-plans")).json() == before


# ===========================================================================
# RLS fail-closed + cross-org isolation for economic_plan / economic_plan_line
# ===========================================================================
class TestM4RLSFailClosed:
    @pytest.mark.asyncio
    async def test_no_context_zero_plans(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM economic_plan"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_no_context_zero_plan_lines(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM economic_plan_line"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_insert_foreign_org_plan_rejected(self, app_rw_session, m4_seed):
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_b_id"])},
        )
        with pytest.raises(Exception):
            await app_rw_session.execute(
                text(
                    "INSERT INTO economic_plan (id, organization_id, property_id, plan_year, "
                    "period_start, period_end, engine_version) "
                    "VALUES (:id, :org, :prop, 2025, '2025-01-01', '2025-12-31', 'm4.1.0')"
                ),
                {"id": str(uuid.uuid4()), "org": str(m4_seed["org_a_id"]), "prop": m4_seed["property"]},
            )

    @pytest.mark.asyncio
    async def test_cross_org_plan_invisible(self, client_a_admin, client_b_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        # org_b cannot see org_a's plan.
        resp = await client_b_admin.get(f"/api/economic-plans/{plan_id}")
        assert resp.status_code == 404


# ===========================================================================
# RBAC -- READ_ONLY cannot create/finalize; can read.
# ===========================================================================
class TestM4Rbac:
    @pytest.mark.asyncio
    async def test_readonly_cannot_create_plan(self, client_a_readonly, m4_seed):
        resp = await client_a_readonly.post("/api/economic-plans", json={
            "property_id": m4_seed["property"], "plan_year": 2025,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_cannot_create_weg_run(self, client_a_admin, client_a_readonly, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_readonly.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 403


# ===========================================================================
# Immutability of economic_plan (ADR-0003 trigger)
# ===========================================================================
class TestM4PlanImmutability:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('column', ['reserve_contribution_total', 'monthly_due_day'])
    async def test_update_finalized_plan_rejected(self, app_rw_session, m4_seed, client_a_admin, column):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text(f"UPDATE economic_plan SET {column} = 3 WHERE id = :id"),
                {"id": plan_id},
            )

    @pytest.mark.asyncio
    async def test_delete_finalized_plan_rejected(self, app_rw_session, m4_seed, client_a_admin):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text("DELETE FROM economic_plan WHERE id = :id"), {"id": plan_id}
            )

    @pytest.mark.asyncio
    async def test_finalized_plan_line_frozen(self, app_rw_session, m4_seed, client_a_admin):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text("UPDATE economic_plan_line SET planned_amount = 1 WHERE economic_plan_id = :id"),
                {"id": plan_id},
            )

    @pytest.mark.asyncio
    async def test_superseded_by_id_allowed_on_finalized_plan(self, app_rw_session, m4_seed, client_a_admin):
        # The ONLY allowed mutation: setting superseded_by_id (correction chain).
        resp = await client_a_admin.post(f"/api/economic-plans/{await _create_and_finalize_plan(client_a_admin, m4_seed)}/correction")
        assert resp.status_code == 201
        # The correction creates a new draft plan and links the original.
        assert resp.json()["status"] == "draft"


# ===========================================================================
# Wirtschaftsplan end-to-end (monthly rates)
# ===========================================================================
class TestM4WirtschaftsplanE2E:
    @pytest.mark.asyncio
    async def test_plan_monthly_rates(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.get(f"/api/economic-plans/{plan_id}")
        data = resp.json()
        # Total volume = 4000 + 3000 + 6000 = 13000. Owner A 600/1000 = 7800;
        # B 400/1000 = 5200. Monthly A 650.00 ; B 433.33 (Σ rates == annual).
        advances = {ln["owner_profile_id"]: ln for ln in data["lines"] if ln["line_type"] == "OWNER_ADVANCE"}
        a = advances[m4_seed["owner_a"]]
        b = advances[m4_seed["owner_b"]]
        assert a["planned_amount"] == "7800.00"
        # 7800/12 = 650.00 exactly.
        assert a["monthly_amount"] == "650.00"
        assert b["planned_amount"] == "5200.00"
        # 5200/12 = 433.3333... ; floor 433.33 * 12 = 5199.96 -> 4 extra cents go
        # to the first 4 rates (Largest-Remainder, ADR-0005): 4x 433.34 + 8x 433.33.
        # monthly_amount is the representative first rate (433.34); the exact 12
        # rates sum to 5200.00. Per-rate detail lives in the plan snapshot.
        assert b["monthly_amount"] == "433.34"

    @pytest.mark.asyncio
    async def test_plan_pdf_only_finalized(self, client_a_admin, m4_seed):
        resp = await client_a_admin.post("/api/economic-plans", json={
            "property_id": m4_seed["property"], "plan_year": 2026,
            "period_start": "2026-01-01", "period_end": "2026-12-31",
        })
        plan_id = resp.json()["id"]
        # Draft -> no PDF.
        resp = await client_a_admin.get(f"/api/economic-plans/{plan_id}/pdf")
        assert resp.status_code == 409


# ===========================================================================
# WEG-Jahresabrechnung end-to-end -- the hand-checked Abrechnungsspitze.
# ===========================================================================
class TestM4WegSettlementE2E:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('reserve,anna_reserve,anna_advance', [
        ('6000.00', '3600.00', '7800.00'), ('6000.01', '3600.01', '7800.01')])
    async def test_full_weg_settlement(self, client_a_admin, m4_withdrawal_seed, app_rw_session,
                                       reserve, anna_reserve, anna_advance):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_withdrawal_seed, reserve)
        # Create the WEG run linked to the plan.
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_withdrawal_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        # Compute + finalize with reserve opening 20000, withdrawal 8000.
        resp = await finalize_weg_fixture(client_a_admin, run_id, m4_withdrawal_seed, "20000.00", "8000.00")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "finalized"
        assert data["snapshot_sha256"] is not None

        # Ist costs (excl. reserve) = Gartenpflege 4000 + Verwalter 3000 = 7000.
        assert data["total_cost"] == "7000.00"

        lines = data["lines"]
        # Per-owner OWNER_COST_TOTAL: A 7000*600/1000 = 4200 ; B = 2800.
        cost_totals = {ln["addressee_owner_profile_id"]: ln["amount"]
                       for ln in lines if ln["line_type"] == "OWNER_COST_TOTAL"}
        assert cost_totals[m4_withdrawal_seed["owner_a"]] == "4200.00"
        assert cost_totals[m4_withdrawal_seed["owner_b"]] == "2800.00"

        # Plan Soll: the plan advance per owner is cost+reserve share:
        #   A 13000*600/1000 = 7800 ; B = 5200.
        plan_adv = {ln["addressee_owner_profile_id"]: ln["amount"]
                    for ln in lines if ln["line_type"] == "PLAN_ADVANCE"}
        assert plan_adv[m4_withdrawal_seed["owner_a"]] == anna_advance
        assert plan_adv[m4_withdrawal_seed["owner_b"]] == "5200.00"

        # Planned reserve is owner Soll, not a cost: both Spitzen are zero.
        spitze = {ln["addressee_owner_profile_id"]: ln["amount"]
                  for ln in lines if ln["line_type"] == "ABRECHNUNGSSPITZE"}
        assert spitze[m4_withdrawal_seed["owner_a"]] == "0.00"
        assert spitze[m4_withdrawal_seed["owner_b"]] == "0.00"
        reserve_soll = {ln["addressee_owner_profile_id"]: ln["amount"]
                        for ln in lines if ln["line_type"] == "RESERVE_SOLL"}
        assert reserve_soll == {m4_withdrawal_seed["owner_a"]: anna_reserve,
                                m4_withdrawal_seed["owner_b"]: "2400.00"}
        assert sum(map(Decimal, reserve_soll.values())) == Decimal(reserve)
        # Each owner's Soll is the sum of that owner's PLANNED rows in the frozen reserve ledger.
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_withdrawal_seed["org_a_id"])},
        )
        payload = (await app_rw_session.execute(
            text("SELECT snapshot_payload FROM settlement_run WHERE id = :id"), {"id": run_id}
        )).scalar_one()
        planned = {}
        for row in payload["reserve_ledger"]["entries"]:
            if row["kind"] == "PLANNED":
                planned[row["owner_profile_id"]] = planned.get(row["owner_profile_id"], Decimal("0.00")) + Decimal(row["amount"])
        assert planned
        assert reserve_soll == {owner: format(amount, ".2f") for owner, amount in planned.items()}
        await app_rw_session.rollback()

        # A reserve cost position is not cash: no owner has paid a contribution.
        res_owner = {ln["addressee_owner_profile_id"]: ln["amount"]
                     for ln in lines if ln["line_type"] == "RESERVE_CONTRIBUTION"
                     and ln["addressee_owner_profile_id"] is not None}
        assert res_owner == {}

        # Cash development: 20000 opening + 0 paid - 8000 override = 12000.
        closing = next(ln for ln in lines if ln["line_type"] == "RESERVE_CLOSING")
        assert closing["amount"] == "12000.00"
        opening = next(ln for ln in lines if ln["line_type"] == "RESERVE_OPENING")
        assert opening["amount"] == "20000.00"

    @pytest.mark.asyncio
    async def test_sum_owner_cost_equals_total(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        resp = await finalize_weg_fixture(client_a_admin, run_id, m4_seed)
        lines = resp.json()["lines"]
        s = sum(Decimal(ln["amount"]) for ln in lines if ln["line_type"] == "OWNER_COST_TOTAL")
        assert s == Decimal("7000.00")

    @pytest.mark.asyncio
    async def test_weg_requires_plan(self, client_a_admin, m4_seed):
        # No economic_plan_id -> the create endpoint requires it (422 on missing).
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": str(uuid.uuid4()),
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        # Unknown plan id -> 404.
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plan_id", [None, "None", "", "not-a-uuid"])
    async def test_weg_refuses_invalid_plan_with_structured_german_message(self, client_a_admin, m4_seed, plan_id):
        response = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == {
            "code": "WEG_PLAN_REQUIRED", "message": "Bitte wählen Sie einen gültigen Wirtschaftsplan.",
        }

    @pytest.mark.asyncio
    async def test_weg_pdf_renders_for_finalized(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await finalize_weg_fixture(client_a_admin, run_id, m4_seed)
        resp = await client_a_admin.get(f"/api/weg-settlements/{run_id}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_finalized_weg_run_cannot_recompute(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await finalize_weg_fixture(client_a_admin, run_id, m4_seed)
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/compute", json=weg_values(m4_seed))
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_payload_consistent_with_lines(self, client_a_admin, m4_seed, app_rw_session):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await finalize_weg_fixture(client_a_admin, run_id, m4_seed)

        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        row = (await app_rw_session.execute(
            text("SELECT snapshot_payload FROM settlement_run WHERE id = :id"), {"id": run_id}
        )).first()
        payload = row[0]
        # Σ owner_cost_total in the payload result == total_cost.
        owners = payload["result"]["owners"]
        s = sum(Decimal(o["owner_cost_total"]) for o in owners)
        assert s == Decimal(payload["result"]["cost_total"])
        # Σ spitze == cost_total - plan_advances_total (no drift).
        s_sp = sum(Decimal(o["abrechnungsspitze"]) for o in owners)
        assert s_sp == Decimal(payload["result"]["abrechnungsspitze_total"])


# ===========================================================================
# Vermoegensbericht end-to-end.
# ===========================================================================
class TestM4VermoegensberichtE2E:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["compute", "finalize"])
    @pytest.mark.parametrize("invalid_inputs", [False, True])
    async def test_out_of_scope_and_unknown_report_bank_accounts_precede_validation(
        self, client_a_admin, client_b_admin, seed_data, migrator_session, action, invalid_inputs,
    ):
        banks = []
        for api, label in ((client_a_admin, "Report"), (client_a_admin, "Other property"), (client_b_admin, "Foreign")):
            property_response = await api.post("/api/properties", json={
                "name": label + " report bank", "management_type": "WEG",
            })
            assert property_response.status_code == 201, property_response.text
            bank = await api.post("/api/bank-accounts", json={
                "property_id": property_response.json()["id"], "account_type": "LAUFEND", "label": label,
            })
            assert bank.status_code == 201, bank.text
            banks.append(bank.json())
        visible_bank, other_property_bank, foreign_bank = banks
        scope_id = uuid.uuid4()
        params = {"scope": str(scope_id), "org": str(seed_data["org_a_id"]),
                  "property": visible_bank["property_id"], "bank": visible_bank["id"],
                  "actor": str(seed_data["user_a_admin_id"])}
        with psycopg2.connect(SYNC_URL) as setup, setup.cursor() as cursor:
            cursor.execute("""INSERT INTO accounting_scope(
                id,organization_id,property_id,scope_type,label,valid_from,whole_property,origin,created_by)
                VALUES(%(scope)s,%(org)s,%(property)s,'WEG','Report scope','2025-01-01',true,'REVIEWED',%(actor)s)""", params)
            cursor.execute("""INSERT INTO accounting_scope_binding(
                organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by)
                VALUES(%(org)s,%(property)s,%(scope)s,%(bank)s,'REVIEWED','Report bank',%(actor)s)""", params)
            cursor.execute("""INSERT INTO scope_grant(
                organization_id,scope_id,user_id,capabilities,granted_by,reason)
                VALUES(%(org)s,%(scope)s,%(actor)s,ARRAY['read','prepare'],%(actor)s,'Report preparation')""", params)
        report = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": visible_bank["property_id"], "accounting_scope_id": str(scope_id),
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert report.status_code == 201, report.text
        run_id = report.json()["id"]
        before = await migrator_session.scalar(text("SELECT to_jsonb(r)::text FROM settlement_run r WHERE id=:id"), {"id": run_id})
        body = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "receivables": [], "payables": [], "bank_balances": [
                    {"bank_account_id": visible_bank["id"], "label": "Visible", "account_type": "LAUFEND", "balance": "0.00"},
                ]}
        if invalid_inputs:
            body["reserve_withdrawals_total"] = "1.00"
        responses = []
        for parent in (foreign_bank["id"], str(uuid.uuid4()), other_property_bank["id"]):
            balance = {"bank_account_id": parent, "label": "Unresolved", "account_type": "LAUFEND", "balance": "0.00"}
            response = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/{action}",
                json=body | {"bank_balances": body["bank_balances"] + [balance]})
            assert response.status_code == 404, response.text
            responses.append(response.json())
        assert responses == [{"detail": "Bank account not found"}] * 3
        assert await migrator_session.scalar(text("SELECT to_jsonb(r)::text FROM settlement_run r WHERE id=:id"), {"id": run_id}) == before
        assert await migrator_session.scalar(text("SELECT count(*) FROM settlement_line WHERE settlement_run_id=:id"), {"id": run_id}) == 0

    @pytest.mark.asyncio
    async def test_full_asset_report(self, client_a_admin, m4_withdrawal_seed):
        resp = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": m4_withdrawal_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        body = {
            "reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "20000.00",
            "bank_balances": [
                {"bank_account_id": m4_withdrawal_seed["bank_giro"], "label": "Girokonto", "account_type": "LAUFEND", "balance": "15000.00"},
                {"bank_account_id": m4_withdrawal_seed["bank_ruecklage"], "label": "Ruecklagenkonto", "account_type": "RUECKLAGE", "balance": "18000.00"},
            ],
            "receivables": [{"label": "Hausgeldrueckstand", "amount": "500.00"}],
            "payables": [{"label": "offene Rechnung", "amount": "1200.00"}],
        }
        evidence = {key.removeprefix('reserve_'): value for key, value in (await reserve_evidence(client_a_admin, m4_withdrawal_seed)).items()}
        for component, amount in [('WITHDRAWAL', '8000.00')]:
            correction = await client_a_admin.post(f"/api/weg-settlements/{run_id}/reserve-ledger/overrides", json={
                'component': component, 'amount': amount, 'reason': 'Documented synthetic correction', **evidence})
            assert correction.status_code == 201, correction.text
        from tests.test_reserve_ledger import payment
        await payment(client_a_admin, m4_withdrawal_seed, '6000.00')
        await attest_fixture(client_a_admin, "settlement", run_id, body)
        resp = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/finalize", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        lines = data["lines"]
        # Reserve closing 18000 (automatic).
        closing = next(ln for ln in lines if ln["line_type"] == "RESERVE_CLOSING")
        assert closing["amount"] == "18000.00"
        # Bank balances captured.
        bank = [ln for ln in lines if ln["line_type"] == "BANK_BALANCE"]
        assert len(bank) == 2
        recv = next(ln for ln in lines if ln["line_type"] == "RECEIVABLE")
        assert recv["amount"] == "500.00"

    @pytest.mark.asyncio
    async def test_asset_report_pdf(self, client_a_admin, m4_seed):
        resp = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": m4_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        body = {
            "reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "1000.00", "receivables": [], "payables": [],
            "bank_balances": [
                {"bank_account_id": m4_seed["bank_giro"], "label": "Giro", "account_type": "LAUFEND", "balance": "500.00"},
                {"bank_account_id": m4_seed["bank_ruecklage"], "label": "Reserve", "account_type": "RUECKLAGE", "balance": "0.00"}],
        }
        await attest_fixture(client_a_admin, "settlement", run_id, body)
        finalized = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/finalize", json=body)
        assert finalized.status_code == 200, finalized.text
        resp = await client_a_admin.get(f"/api/weg-settlements/asset-reports/{run_id}/pdf")
        assert resp.status_code == 200
        assert resp.content[:4] == b"%PDF"


# ===========================================================================
# Snapshot/hash determinism through the service (m4 section 9.1 #9).
# ===========================================================================
class TestM4Determinism:
    @pytest.mark.asyncio
    async def test_weg_results_stable_with_distinct_attestations(self, client_a_admin, m4_withdrawal_seed):
        # Identical arithmetic retains separate evidence for each attestation.
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_withdrawal_seed)

        async def finalize_once():
            resp = await client_a_admin.post("/api/weg-settlements", json={
                "property_id": m4_withdrawal_seed["property"], "economic_plan_id": plan_id,
                "period_start": "2025-01-01", "period_end": "2025-12-31",
            })
            run_id = resp.json()["id"]
            ledger = (await client_a_admin.get(f"/api/weg-settlements/{run_id}/reserve-ledger")).json()
            if ledger['frozen']:
                # Reuse the issued same-period opening; do not attest another import.
                values = {'paid_advances': weg_values(m4_withdrawal_seed)['paid_advances']}
                await attest_fixture(client_a_admin, 'settlement', run_id, values)
                resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json=values)
                assert resp.status_code == 200, resp.text
            else:
                resp = await finalize_weg_fixture(client_a_admin, run_id, m4_withdrawal_seed, "20000.00", "8000.00")
            return resp.json()

        first = await finalize_once()
        second = await finalize_once()
        assert first["total_cost"] == second["total_cost"]
        assert [(l["line_type"], l["amount"]) for l in first["lines"]] == [(l["line_type"], l["amount"]) for l in second["lines"]]
        assert first["snapshot_sha256"] != second["snapshot_sha256"]
        reloaded = await client_a_admin.get(f"/api/weg-settlements/{first['id']}")
        assert reloaded.json()["snapshot_sha256"] == first["snapshot_sha256"]


# ===========================================================================

# ===========================================================================
# T09: derive_paid_advances – test paid-advances-derived route
# ===========================================================================
from datetime import datetime, timezone as _tz

_TZ_UTC = _tz.utc
_BOOKED_AT = datetime(2025, 6, 2, 10, 0, 0, tzinfo=_TZ_UTC)


def _insert_payment_sql() -> str:
    return """
        INSERT INTO payment (
            id, organization_id, bank_account_id, property_id,
            direction, amount, value_date, payment_type,
            tenant_profile_id, owner_profile_id, lease_agreement_id,
            period_year, period_month, status, booked_at, booked_by
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """


@pytest.fixture(scope="session")
def m4_t09_seed(seed_data):
    """Keep booked payments separate from the no-payments fixture."""
    m4_seed = _seed_weg(seed_data)
    import psycopg2

    org = str(m4_seed["org_a_id"])
    prop = str(m4_seed["property"])
    owner_a = str(m4_seed["owner_a"])
    owner_b = str(m4_seed["owner_b"])
    bank_giro = str(m4_seed["bank_giro"])

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        # 12 monthly payments per owner: 600.00 for owner A, 500.00 for owner B
        for month in range(1, 13):
            # Owner A: 12 x 600 = 7200
            cur.execute(
                _insert_payment_sql(),
                (
                    str(uuid.uuid4()),
                    org,
                    bank_giro,
                    prop,
                    "IN",
                    "600.00",
                    date(2025, month, 1),
                    "HAUSGELD_VORSCHUSS",
                    None,
                    owner_a,
                    None,
                    2025,
                    month,
                    "booked",
                    _BOOKED_AT,
                    str(m4_seed["user_a_admin_id"]),
                ),
            )
            # Owner B: 12 x 500 = 6000
            cur.execute(
                _insert_payment_sql(),
                (
                    str(uuid.uuid4()),
                    org,
                    bank_giro,
                    prop,
                    "IN",
                    "500.00",
                    date(2025, month, 1),
                    "HAUSGELD_VORSCHUSS",
                    None,
                    owner_b,
                    None,
                    2025,
                    month,
                    "booked",
                    _BOOKED_AT,
                    str(m4_seed["user_a_admin_id"]),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return m4_seed


class TestM4PaidAdvancesDerived:
    @pytest.mark.asyncio
    async def test_derive_paid_advances_returns_correct_totals(self, client_a_admin, m4_t09_seed):
        """12 monthly payments per owner → correct paid totals."""
        # Create and finalize economic plan
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_t09_seed)

        # Create settlement run
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_t09_seed["property"],
            "economic_plan_id": plan_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]

        # Derive paid advances
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/paid-advances-derived")
        assert resp.status_code == 200, resp.text
        result = resp.json()

        # Should return 2 owners with correct totals
        assert len(result) == 2
        totals = {item["owner_profile_id"]: item["paid_total"] for item in result}
        # Owner A: 12 x 600 = 7200
        assert totals[str(m4_t09_seed["owner_a"])] == "7200.00"
        # Owner B: 12 x 500 = 6000
        assert totals[str(m4_t09_seed["owner_b"])] == "6000.00"

    @pytest.mark.asyncio
    async def test_derive_paid_advances_returns_zero_totals_when_no_payments(self, client_a_admin, m4_seed):
        """No payments → 2 owners with paid_total='0.00'."""
        # Create and finalize economic plan (no payments added)
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)

        # Create settlement run
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"],
            "economic_plan_id": plan_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]

        # Derive paid advances → 2 owners with 0.00
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/paid-advances-derived")
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert len(result) == 2
        totals = {item["owner_profile_id"]: item["paid_total"] for item in result}
        assert totals[str(m4_seed["owner_a"])] == "0.00"
        assert totals[str(m4_seed["owner_b"])] == "0.00"


@pytest.mark.asyncio
async def test_weg_balance_reads_booked_payments_and_freezes_them(client_a_admin, m4_seed, app_rw_session):
    from app.engine.canonical import canonical_json, sha256_hex
    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w)
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    async def payment(amount, year=2025, action='book', direction='IN'):
        response = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'],
            'owner_profile_id': w['owner_a'], 'direction': direction, 'amount': amount,
            'value_date': '2025-12-01', 'payment_type': 'HAUSGELD_VORSCHUSS', 'period_year': year})
        assert response.status_code == 201, response.text
        row = response.json()
        if action != 'draft':
            response = await api.post(f"/api/payments/{row['id']}/book")
            assert response.status_code == 200, response.text
        if action == 'reverse':
            response = await api.post(f"/api/payments/{row['id']}/reverse", json={'note': 'Synthetic correction'})
            assert response.status_code == 201, response.text
        return row
    await payment('7500.00')
    await payment('100.00', direction='OUT')
    await payment('1000.00', action='draft')
    await payment('2000.00', action='reverse')
    await payment('3000.00', year=2024)
    # The same owner can own another property; its bookings must not leak in.
    other = _seed_weg(w)
    response = await api.post('/api/units', json={'property_id': other['property'], 'number': '3',
        'usage_type': 'WOHNUNG'})
    assert response.status_code == 201, response.text
    response = await api.post('/api/ownership', json={'unit_id': response.json()['id'],
        'owner_profile_id': w['owner_a'], 'valid_from': '2020-01-01'})
    assert response.status_code == 201, response.text
    response = await api.post('/api/payments', json={'bank_account_id': other['bank_giro'],
        'owner_profile_id': w['owner_a'], 'direction': 'IN', 'amount': '888.00',
        'value_date': '2025-12-01', 'payment_type': 'HAUSGELD_VORSCHUSS', 'period_year': 2025})
    assert response.status_code == 201, response.text
    assert (await api.post(f"/api/payments/{response.json()['id']}/book")).status_code == 200
    before = (await api.get('/api/payments')).json()
    values = weg_values(w)
    values['paid_advances'][0]['paid_total'] = '99999.00'
    await attest_fixture(api, 'settlement', run, values)
    response = await api.post(f'/api/weg-settlements/{run}/finalize', json=values)
    assert response.status_code == 200, response.text
    result = response.json()
    owner = next(o for o in result['owner_results'] if o['owner_profile_id'] == w['owner_a'])
    assert (owner['plan_soll'], owner['paid'], owner['account_balance'], owner['payment_amount']) == (
        '7800.00', '7400.00', '400.00', '400.00')
    assert owner['abrechnungsspitze'] == '0.00'
    assert (await api.get('/api/payments')).json() == before
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org': str(w['org_a_id'])})
    payload = (await app_rw_session.execute(text('SELECT snapshot_payload FROM settlement_run WHERE id=:id'), {'id': run})).scalar_one()
    assert sha256_hex(canonical_json(payload)) == result['snapshot_sha256']
    await app_rw_session.rollback()
    frozen_pdf = (await api.get(f'/api/weg-settlements/{run}/pdf')).content
    await payment('500.00')
    await payment('8000.00', direction='OUT')
    derived = (await api.post(f'/api/weg-settlements/{run}/paid-advances-derived')).json()
    assert next(row['paid_total'] for row in derived if row['owner_profile_id'] == w['owner_a']) == '-100.00'
    reloaded = (await api.get(f'/api/weg-settlements/{run}')).json()
    assert reloaded['owner_results'] == result['owner_results']
    assert reloaded['snapshot_sha256'] == result['snapshot_sha256']
    assert (await api.get(f'/api/weg-settlements/{run}/pdf')).content == frozen_pdf


@pytest.mark.asyncio
@pytest.mark.parametrize('engine_version,has_balance', [('m4.2.0', False), ('m4.3.0', True), ('m4.3.2', True)])
async def test_legacy_weg_pdf_and_digest_are_not_upgraded_on_read(client_a_admin, m4_seed, app_rw_session, monkeypatch, engine_version, has_balance):
    from app.engine.canonical import canonical_json, sha256_hex
    from app.services import weg_settlement_service as service
    from tests.test_reserve_ledger import pdf_text
    original_builder = service.build_weg_snapshot_payload
    original_compute = service.compute_weg_jahresabrechnung
    def legacy_compute(inp):
        from dataclasses import replace
        if hasattr(inp, "plan_reserve"):
            inp = replace(inp, plan_reserve=())
        return original_compute(inp)
    monkeypatch.setattr(service, "compute_weg_jahresabrechnung", legacy_compute)
    def legacy_builder(inp, result):
        payload = original_builder(inp, result)
        for owner in payload['result']['owners']:
            owner.pop('plan_reserve_soll', None)
            if not has_balance:
                for key in ('plan_soll', 'paid', 'account_balance', 'payment_amount'):
                    owner.pop(key)
        return payload
    monkeypatch.setattr(service, 'ENGINE_VERSION', engine_version)
    monkeypatch.setattr(service, 'build_weg_snapshot_payload', legacy_builder)
    plan = await _create_and_finalize_plan(client_a_admin, m4_seed)
    response = await client_a_admin.post('/api/weg-settlements', json={'property_id': m4_seed['property'],
        'economic_plan_id': plan, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    frozen = (await finalize_weg_fixture(client_a_admin, run, m4_seed)).json()
    assert not any(ln['line_type'] == 'RESERVE_SOLL' for ln in frozen['lines'])
    assert sorted(ln['amount'] for ln in frozen['lines'] if ln['line_type'] == 'ABRECHNUNGSSPITZE') == ['-2400.00', '-3600.00']
    original_pdf = (await client_a_admin.get(f'/api/weg-settlements/{run}/pdf')).content
    monkeypatch.undo()
    def refuse_recompute(*args, **kwargs):
        raise AssertionError('A finalized run must never be recomputed')
    monkeypatch.setattr(service, 'compute_weg_jahresabrechnung', refuse_recompute)
    assert (await client_a_admin.post(f'/api/weg-settlements/{run}/compute', json=weg_values(m4_seed))).status_code == 409
    response = await client_a_admin.get(f'/api/weg-settlements/{run}/pdf')
    assert response.status_code == 200 and response.content.startswith(b'%PDF')
    assert response.content == original_pdf
    assert ('Zahlbetrag' in pdf_text(response.content)) == has_balance
    reloaded = (await client_a_admin.get(f'/api/weg-settlements/{run}')).json()
    assert reloaded['owner_results'] == frozen['owner_results']
    assert reloaded['engine_version'] == engine_version
    assert reloaded['snapshot_sha256'] == frozen['snapshot_sha256']
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org': str(m4_seed['org_a_id'])})
    payload = (await app_rw_session.execute(text('SELECT snapshot_payload FROM settlement_run WHERE id=:id'), {'id': run})).scalar_one()
    assert sha256_hex(canonical_json(payload)) == frozen['snapshot_sha256']
    assert all(('payment_amount' in o) == has_balance for o in payload['result']['owners'])
    assert all('plan_reserve_soll' not in o for o in payload['result']['owners'])


@pytest.mark.asyncio
async def test_weg_owner_change_keeps_each_owners_own_bookings(client_a_admin, m4_seed):
    api, w = client_a_admin, m4_seed
    old = (await api.get('/api/ownership', params={'unit_id': w['u1']})).json()[0]
    response = await api.patch(f"/api/ownership/{old['id']}", json={'valid_to': '2025-06-30'})
    assert response.status_code == 200, response.text
    person = await api.post('/api/persons', json={'first_name': 'Clara', 'last_name': 'Successor'})
    assert person.status_code == 201, person.text
    owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    assert owner.status_code == 201, owner.text
    successor = owner.json()['id']
    response = await api.post('/api/ownership', json={'unit_id': w['u1'], 'owner_profile_id': successor,
        'valid_from': '2025-07-01'})
    assert response.status_code == 201, response.text
    plan = await _create_and_finalize_plan(api, w)
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    for oid, amount, month in ((w['owner_a'], '1600.00', 6), (successor, '1900.00', 7)):
        response = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'],
            'owner_profile_id': oid, 'direction': 'IN', 'amount': amount, 'value_date': f'2025-{month:02d}-01',
            'payment_type': 'HAUSGELD_VORSCHUSS', 'period_year': 2025, 'period_month': month})
        assert response.status_code == 201, response.text
        assert (await api.post(f"/api/payments/{response.json()['id']}/book")).status_code == 200
    derived = (await api.post(f'/api/weg-settlements/{run}/paid-advances-derived')).json()
    assert {r['owner_profile_id']: r['paid_total'] for r in derived} == {
        w['owner_a']: '1600.00', w['owner_b']: '0.00', successor: '1900.00'}
    values = {'paid_advances': derived}
    await attest_fixture(api, 'settlement', run, values)
    response = await api.post(f'/api/weg-settlements/{run}/finalize', json=values)
    assert response.status_code == 200, response.text
    results = {o['owner_profile_id']: o for o in response.json()['owner_results']}
    assert results[w['owner_a']]['paid'] == '1600.00'
    assert results[successor]['paid'] == '1900.00'
    assert results[w['owner_a']]['ownership_periods'] == [{'unit_number': '1', 'valid_from': '2025-01-01', 'valid_to': '2025-06-30'}]
    assert results[successor]['ownership_periods'] == [{'unit_number': '1', 'valid_from': '2025-07-01', 'valid_to': '2025-12-31'}]
    from tests.test_reserve_ledger import pdf_text
    content = pdf_text((await api.get(f'/api/weg-settlements/{run}/pdf')).content)
    assert '01.01.2025 bis 30.06.2025' in content and '01.07.2025 bis 31.12.2025' in content


@pytest.mark.asyncio
@pytest.mark.parametrize('transfer', ['2025-01-01', '2025-12-31', '2025-07-01'])
async def test_documents_resolve_owners_on_their_key_date_and_freeze_them(
    client_a_admin, client_b_admin, m4_seed, migrator_session, transfer,
):
    from tests.test_reserve_ledger import pdf_text
    old, new, unit = m4_seed['owner_b'], m4_seed['owner_a'], m4_seed['u2']
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE ownership_history SET valid_to=%s WHERE unit_id=%s', (transfer, unit))
            cur.execute('INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from) '
                'VALUES (%s,%s,%s,%s,%s)', (str(uuid.uuid4()), str(m4_seed['org_a_id']), unit, new, transfer))
    plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
    response = await client_a_admin.post('/api/weg-settlements/asset-reports', json={
        'property_id': m4_seed['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    report_id = response.json()['id']
    body = {'bank_balances': [{'bank_account_id': m4_seed['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': m4_seed['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'}],
        'receivables': [], 'payables': []}
    await attest_fixture(client_a_admin, 'settlement', report_id, body)
    response = await client_a_admin.post(f'/api/weg-settlements/asset-reports/{report_id}/finalize', json=body)
    assert response.status_code == 200, response.text
    frozen = []
    for path, expected, key in [(f'economic-plans/{plan_id}', new if transfer == '2025-01-01' else old, '2025-01-01'),
                                (f'weg-settlements/asset-reports/{report_id}', new, '2025-12-31')]:
        resource = path.replace('weg-settlements/asset-reports/', 'weg-settlements/')
        response = await client_a_admin.get('/api/' + resource)
        assert response.status_code == 200, response.text
        recipients = response.json()['addressees']
        assert len(recipients) == 2  # One per unit, including the unchanged unit 1.
        recipient = next(r for r in recipients if r['unit_id'] == unit)
        assert recipient['owner_profile_id'] == expected and recipient['key_date'] == key
        assert (await client_b_admin.get('/api/' + resource)).status_code == 404
        assert (await client_b_admin.get('/api/' + path + '/pdf')).status_code == 404
        pdf = (await client_a_admin.get('/api/' + path + '/pdf')).content
        assert 'Eigentümer am ' + '.'.join(reversed(key.split('-'))) in pdf_text(pdf)
        frozen.append((path, response.json(), pdf))
    # Later master-data changes must not change finalized recipients or PDFs.
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE person SET first_name='Changed' WHERE id=%s", (m4_seed['person_a'],))
    for path, data, pdf in frozen:
        assert (await client_a_admin.get('/api/' + path.replace('weg-settlements/asset-reports/', 'weg-settlements/'))).json() == data
        assert (await client_a_admin.get('/api/' + path + '/pdf')).content == pdf


@pytest.mark.asyncio
async def test_asset_report_recipients_have_authoritative_retention_bindings(worker_tenant, client_b_admin):
    # The whole-organization planner must use this report's bounded corpus,
    # independently of unrelated financial fixtures accumulated by the shard.
    client_a_admin, seed = worker_tenant
    m4_seed = _seed_weg(seed)
    response = await client_a_admin.post('/api/weg-settlements/asset-reports', json={
        'property_id': m4_seed['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    report_id = response.json()['id']
    body = {'bank_balances': [{'bank_account_id': m4_seed['bank_giro'], 'label': 'Giro', 'account_type': 'LAUFEND', 'balance': '0.00'},
        {'bank_account_id': m4_seed['bank_ruecklage'], 'label': 'Reserve', 'account_type': 'RUECKLAGE', 'balance': '0.00'}],
        'receivables': [], 'payables': []}
    await attest_fixture(client_a_admin, 'settlement', report_id, body)
    response = await client_a_admin.post(f'/api/weg-settlements/asset-reports/{report_id}/finalize', json=body)
    assert response.status_code == 200, response.text
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT entity FROM privacy_retention_facts(%s,%s) WHERE kind=%s',
                (str(m4_seed['org_a_id']), m4_seed['person_b'], 'settlement_run'))
            assert report_id in {str(row[0]) for row in cur.fetchall()}
            cur.execute("SELECT retention_until FROM organization_retention_plan(%s) WHERE entity='person' AND identity->>'id'=%s",
                (str(m4_seed['org_a_id']), m4_seed['person_b']))
            assert cur.fetchone()[0] == date(2034, 12, 31)

            cur.execute('UPDATE ownership_history SET valid_to=%s WHERE owner_profile_id=%s',
                ('2026-01-01', m4_seed['owner_b']))
    url = f"/api/dsgvo/persons/{m4_seed['person_b']}"
    before_pdf = (await client_a_admin.get(f'/api/weg-settlements/asset-reports/{report_id}/pdf')).content
    status = await client_a_admin.get(url + '/status')
    assert status.status_code == 200, status.text
    assert status.json()['decision'] == 'RESTRICT'
    assert status.json()['governing_retention_until'] == '2034-12-31'
    assert any(b['entity_id'] == report_id for b in status.json()['bindings'])
    assert (await client_b_admin.get(url + '/status')).status_code == 404
    deletion = await client_a_admin.post(url + '/request-deletion')
    assert deletion.status_code == 200, deletion.text
    assert deletion.json()['applied'] == 'RESTRICTED'
    disclosure = await client_a_admin.get(url + '/export')
    assert disclosure.status_code == 200, disclosure.text
    assert disclosure.json()['person']['first_name'] == 'Berta'
    assert (await client_a_admin.get(f'/api/weg-settlements/asset-reports/{report_id}/pdf')).content == before_pdf
    from importlib import import_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    engine = create_engine(SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin(), Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='Cannot remove retained'):
                import_module('migrations.versions.0162_report_recipient_retention').downgrade()
    finally:
        engine.dispose()


@pytest.mark.parametrize("path, message", [
    ("economic-plans", "Wirtschaftsplan nicht gefunden."),
    ("payments", "Zahlung nicht gefunden."),
    ("weg-settlements", "WEG-Abrechnung nicht gefunden."),
    ("weg-settlements/{id}/input-owners", "WEG-Abrechnung nicht gefunden."),
])
async def test_missing_detail_has_exact_german_message(client_a_admin, client_unauth, path, message):
    missing = "00000000-0000-0000-0000-000000000000"
    path = path.format(id=missing) if "{id}" in path else f"{path}/{missing}"
    response = await client_a_admin.get(f"/api/{path}")
    assert response.status_code == 404
    assert response.json() == {"detail": message}
    assert (await client_unauth.get(f"/api/{path}")).status_code == 401


@pytest.mark.asyncio
async def test_foreign_details_match_missing_german_responses(client_a_admin, client_b_admin, m4_seed):
    plan = await _create_and_finalize_plan(client_a_admin, m4_seed)
    payment = await client_a_admin.post("/api/payments", json={
        "bank_account_id": m4_seed["bank_giro"], "owner_profile_id": m4_seed["owner_a"],
        "direction": "IN", "amount": "600.00", "value_date": "2026-01-05",
        "payment_type": "HAUSGELD_VORSCHUSS"})
    assert payment.status_code == 201, payment.text
    run = await client_a_admin.post("/api/weg-settlements", json={
        "property_id": m4_seed["property"], "economic_plan_id": plan,
        "period_start": "2025-01-01", "period_end": "2025-12-31"})
    assert run.status_code == 201, run.text
    for path, record_id, message in [
        ("economic-plans/{id}", plan, "Wirtschaftsplan nicht gefunden."),
        ("payments/{id}", payment.json()["id"], "Zahlung nicht gefunden."),
        ("weg-settlements/{id}", run.json()["id"], "WEG-Abrechnung nicht gefunden."),
        ("weg-settlements/{id}/input-owners", run.json()["id"], "WEG-Abrechnung nicht gefunden."),
    ]:
        foreign = await client_b_admin.get("/api/" + path.format(id=record_id))
        missing = await client_b_admin.get("/api/" + path.format(id=uuid.UUID(int=0)))
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": message}


# Resolution authority for special levies uses the existing frozen-levy fixtures.
from tests.test_special_levy import (  # noqa: E402,F401
    levy_db, levy_fixture, scope_seed, worker_tenant, client as levy_client,
    body as levy_body, create as create_levy,
)


@pytest.mark.asyncio
async def test_resolution_levy_missing_reference_creates_nothing(levy_db, levy_fixture):
    ids = levy_fixture
    async with levy_client(ids) as api:
        for supplied in (False, True):
            payload = levy_body(ids)
            if supplied:
                payload['resolution_id'] = None
            else:
                payload.pop('resolution_id')
            answer = await api.post('/api/special-levies', json=payload)
            assert answer.status_code == 422, answer.text
            assert answer.json()['detail'] == 'Eine Sonderumlage braucht einen Beschluss.'
    assert await levy_db.scalar(text('SELECT count(*) FROM special_levy WHERE organization_id=:org'), ids) == 0
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE organization_id=:org AND entity_type='special_levy'"), ids) == 0


async def legacy_levy(db, ids):
    """Synthetic historical row through the trusted migrator, never ordinary creation."""
    levy = uuid.uuid4()
    params = {**ids, 'levy': levy}
    await db.execute(text("INSERT INTO special_levy(id,organization_id,property_id,accounting_scope_id,amount,due_date) VALUES (:levy,:org,:property,:scope,1200,'2026-07-01')"), params)
    for unit, owner, history, mea in zip(ids['units'], ids['owners'], ids['histories'], ids.get('legacy_meas', [300, 300, 400]), strict=True):
        await db.execute(text("INSERT INTO special_levy_allocation(organization_id,property_id,special_levy_id,unit_id,owner_profile_id,ownership_history_id,mea,amount) VALUES (:org,:property,:levy,:unit,:owner,:history,:mea,:amount)"),
            {**params, 'unit': unit, 'owner': owner, 'history': history, 'mea': mea, 'amount': Decimal('1.20') * mea})
    await db.commit()
    return levy


@pytest.mark.asyncio
async def test_resolution_levy_links_once_and_preserves_all_frozen_evidence(levy_db, levy_fixture):
    ids = levy_fixture
    levy = await legacy_levy(levy_db, ids)
    params = {'levy': levy}
    before = await levy_db.scalar(text('SELECT to_jsonb(l) FROM special_levy l WHERE id=:levy'), params)
    allocations = (await levy_db.execute(text('SELECT to_jsonb(a) FROM special_levy_allocation a WHERE special_levy_id=:levy ORDER BY id'), params)).scalars().all()
    await levy_db.commit()
    async with levy_client(ids) as api:
        route = f'/api/special-levies/{levy}/resolution'
        for _ in range(2):
            answer = await api.put(route, json={'resolution_id': str(ids['resolution'])})
            assert answer.status_code == 200, answer.text
            assert answer.json()['resolution_id'] == str(ids['resolution'])
        for reference in (None, str(uuid.uuid4())):
            answer = await api.put(route, json={'resolution_id': reference})
            assert answer.status_code in (409, 422), answer.text
    after = await levy_db.scalar(text('SELECT to_jsonb(l) FROM special_levy l WHERE id=:levy'), params)
    assert {k: v for k, v in after.items() if k != 'resolution_id'} == {k: v for k, v in before.items() if k != 'resolution_id'}
    assert (await levy_db.execute(text('SELECT to_jsonb(a) FROM special_levy_allocation a WHERE special_levy_id=:levy ORDER BY id'), params)).scalars().all() == allocations
    assert await levy_db.scalar(text("SELECT count(*) FROM audit_event WHERE entity_id=:levy AND action='special_levy.resolution_linked'"), params) == 1


@pytest.mark.asyncio
async def test_resolution_levy_rejects_unadopted_sources_for_creation_and_link(levy_db, levy_fixture):
    ids = levy_fixture
    await levy_db.execute(text("UPDATE resolution SET result='ABGELEHNT' WHERE id=:resolution"), ids)
    levy = await legacy_levy(levy_db, ids)
    async with levy_client(ids) as api:
        answer = await api.post('/api/special-levies', json=levy_body(ids))
        assert answer.status_code == 422, answer.text
        answer = await api.put(f'/api/special-levies/{levy}/resolution', json={'resolution_id': str(ids['resolution'])})
        assert answer.status_code == 422, answer.text
    assert await levy_db.scalar(text('SELECT resolution_id FROM special_levy WHERE id=:id'), {'id': levy}) is None


@pytest.mark.asyncio
async def test_resolution_levy_concurrent_link_is_atomic(levy_db, levy_fixture):
    import asyncio
    ids = levy_fixture
    levy = await legacy_levy(levy_db, ids)
    other = uuid.uuid4()
    await levy_db.execute(text("INSERT INTO resolution(id,organization_id,meeting_id,wording,votes_yes,votes_no,votes_abstain,result) VALUES (:id,:org,:meeting,'Second authority',2,0,0,'ANGENOMMEN')"), {**ids, 'id': other})
    await levy_db.commit()
    async def link(reference):
        async with levy_client(ids) as api:
            return await api.put(f'/api/special-levies/{levy}/resolution', json={'resolution_id': str(reference)})
    answers = await asyncio.gather(link(ids['resolution']), link(other))
    assert sorted(a.status_code for a in answers) == [200, 409]
    winner = next(a.json()['resolution_id'] for a in answers if a.status_code == 200)
    answers = await asyncio.gather(link(winner), link(winner))
    assert [a.status_code for a in answers] == [200, 200]
    assert await levy_db.scalar(text('SELECT resolution_id::text FROM special_levy WHERE id=:id'), {'id': levy}) == winner


@pytest.mark.asyncio
async def test_resolution_levy_foreign_property_missing_and_readonly_links_are_refused(levy_db, levy_fixture, client_b_admin):
    ids = levy_fixture
    levy = await legacy_levy(levy_db, ids)
    foreign = uuid.uuid4()
    meeting = uuid.uuid4()
    await levy_db.execute(text("INSERT INTO owners_meeting(id,organization_id,property_id,meeting_date,location,meeting_type,status) VALUES (:id,:org,:other_property,'2026-05-15','','ORDENTLICH','HELD')"), {**ids, 'id': meeting})
    await levy_db.execute(text("INSERT INTO resolution(id,organization_id,meeting_id,wording,votes_yes,votes_no,votes_abstain,result) VALUES (:id,:org,:meeting,'Foreign roof',2,0,0,'ANGENOMMEN')"), {**ids, 'id': foreign, 'meeting': meeting})
    await levy_db.commit()
    route = f'/api/special-levies/{levy}/resolution'
    response = await client_b_admin.post('/api/properties', json={'name': 'Foreign authority', 'management_type': 'WEG'})
    assert response.status_code == 201, response.text
    response = await client_b_admin.post('/api/meetings', json={'property_id': response.json()['id'],
        'meeting_date': '2026-05-15', 'meeting_type': 'ORDENTLICH', 'location': 'Foreign meeting'})
    assert response.status_code == 201, response.text
    response = await client_b_admin.post('/api/meetings/resolutions', json={'meeting_id': response.json()['id'],
        'wording': 'Foreign authority', 'votes_yes': 1, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
    assert response.status_code == 201, response.text
    foreign_org = uuid.UUID(response.json()['id'])
    async with levy_client(ids) as api:
        for reference in (foreign, foreign_org, uuid.uuid4()):
            response = await api.post('/api/special-levies', json=levy_body(ids, resolution_id=str(reference)))
            assert response.status_code == 404, response.text
            response = await api.put(route, json={'resolution_id': str(reference)})
            assert response.status_code == 404, response.text
    async with levy_client(ids, 'readonly') as api:
        assert (await api.put(route, json={'resolution_id': str(ids['resolution'])})).status_code == 403
    assert (await client_b_admin.put(route, json={'resolution_id': str(ids['resolution'])})).status_code == 404
    assert (await client_b_admin.post('/api/special-levies', json=levy_body(ids))).status_code == 404
    assert await levy_db.scalar(text('SELECT resolution_id FROM special_levy WHERE id=:id'), {'id': levy}) is None


@pytest.mark.asyncio
async def test_resolution_levy_register_does_not_disclose_without_financial_read(levy_db, levy_fixture):
    ids = levy_fixture
    async with levy_client(ids) as api:
        levy = await create_levy(api, ids)
        entry = await api.post(f"/api/meetings/resolutions/{ids['resolution']}/register")
        assert entry.status_code == 201, entry.text
        answer = await api.get(f"/api/meetings/register/{ids['property']}")
        assert answer.status_code == 200
        assert answer.json()[0]['special_levies'][0]['id'] == levy['id']
    await levy_db.execute(text('UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor WHERE id=:grant'), ids)
    await levy_db.commit()
    async with levy_client(ids) as api:
        answer = await api.get(f"/api/meetings/register/{ids['property']}")
        assert answer.status_code == 200, answer.text
        assert answer.json()[0]['special_levies'] == []
        assert (await api.put(f"/api/special-levies/{levy['id']}/resolution", json={'resolution_id': str(ids['resolution'])})).status_code == 404


@pytest.mark.asyncio
async def test_resolution_levy_database_link_keeps_every_other_column_immutable(levy_db, levy_fixture):
    from tests.test_financial_scope_boundary import actor
    from sqlalchemy.exc import DBAPIError
    ids = levy_fixture
    levy = await legacy_levy(levy_db, ids)
    ids['levy'] = levy
    await actor(levy_db, ids)
    await levy_db.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(ids['scope'])})
    for statement in (
        'UPDATE special_levy SET resolution_id=:resolution,amount=1 WHERE id=:levy',
        'UPDATE special_levy SET resolution_id=NULL WHERE id=:levy',
        'UPDATE special_levy_allocation SET amount=1 WHERE special_levy_id=:levy',
    ):
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text(statement), ids)
    await levy_db.execute(text('UPDATE special_levy SET resolution_id=:resolution WHERE id=:levy'), ids)
    for statement in ('UPDATE special_levy SET resolution_id=NULL WHERE id=:levy',
        'UPDATE special_levy SET resolution_id=:resolution WHERE id=:levy'):
        with pytest.raises(DBAPIError):
            async with levy_db.begin_nested():
                await levy_db.execute(text(statement), ids)
    await levy_db.rollback()


def test_resolution_levy_migration_roundtrip_restores_guard_and_column_grants(run_migrations):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    import importlib
    migration = importlib.import_module('migrations.versions.0168_special_levy_resolution')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            point = db.begin_nested()
            try:
                before = db.scalar(text("SELECT pg_get_functiondef('guard_special_levy()'::regprocedure)"))
                assert db.scalar(text("SELECT has_column_privilege('app_rw','special_levy','resolution_id','UPDATE')"))
                migration.downgrade()
                assert not db.scalar(text("SELECT has_column_privilege('app_rw','special_levy','resolution_id','UPDATE')"))
                migration.upgrade()
                assert db.scalar(text("SELECT pg_get_functiondef('guard_special_levy()'::regprocedure)")) == before
                assert not db.scalar(text("SELECT has_column_privilege('app_rw','special_levy','amount','UPDATE')"))
            finally:
                point.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_resolution_levy_legacy_account_and_dunning_remain_frozen_after_link(levy_db, levy_fixture):
    ids = levy_fixture
    levy = await legacy_levy(levy_db, ids)
    params = {'accounting_scope_id': str(ids['scope']), 'period_start': '2026-07-01', 'period_end': '2026-07-31'}
    async with levy_client(ids) as api:
        path = f"/api/payments/mietkonto/owner/{ids['owners'][0]}"
        before = await api.get(path, params=params)
        assert before.status_code == 200 and before.json()['balance'] == '360.00', before.text
        notice = await api.post('/api/dunning/owner', json={**params,
            'owner_profile_id': str(ids['owners'][0]), 'reference_date': '2026-07-02'})
        assert notice.status_code == 201, notice.text
        assert notice.json()['principal_amount'] == '360.00'
        frozen = await levy_db.scalar(text('SELECT to_jsonb(n) FROM dunning_notice n WHERE id=:id'), {'id': uuid.UUID(notice.json()['id'])})
        await levy_db.commit()
        linked = await api.put(f'/api/special-levies/{levy}/resolution', json={'resolution_id': str(ids['resolution'])})
        assert linked.status_code == 200, linked.text
        after = await api.get(path, params=params)
        assert after.status_code == 200 and after.json() == before.json()
    assert await levy_db.scalar(text('SELECT to_jsonb(n) FROM dunning_notice n WHERE id=:id'), {'id': uuid.UUID(notice.json()['id'])}) == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['compute', 'finalize', 'coverage'])
@pytest.mark.parametrize('value', ['', '   ', 'not-a-number', 'NaN', 'Infinity'])
async def test_paid_amount_errors_identify_the_owner_in_german(client_a_admin, client_b_admin, m4_seed, action, value):
    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w)
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'],
        'economic_plan_id': plan, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    body = {'paid_advances': [{'owner_profile_id': w['owner_a'], 'paid_total': value},
                             {'owner_profile_id': w['owner_b'], 'paid_total': '0.00'}]}
    url = f'/api/weg-settlements/{run}/{action}'
    if action == 'coverage':
        url = '/api/financial-input-coverage'
        body = {'subject_kind': 'settlement', 'subject_id': run, 'values': body,
                'reason': 'Reviewed synthetic payments', 'source_document_ids': [], 'confirmed_complete': True}
    response = await api.post(url, json=body)
    assert response.status_code == 422, response.text
    detail = response.json()['detail']
    assert isinstance(detail, dict), detail
    assert detail['field'] == 'paid_total'
    assert detail['owner_profile_id'] == w['owner_a']
    assert 'Hausgeld' in detail['message'] and 'Invalid decimal' not in detail['message']
    if not value.strip():
        assert detail['code'] == 'financial_inputs_incomplete'
        assert detail['blockers'][0]['code'] == 'owner_payment_coverage_missing'
    else:
        assert detail['code'] == 'invalid_decimal'
    foreign = await client_b_admin.post(url, json=body)
    assert foreign.status_code == 404, foreign.text


@pytest.mark.asyncio
async def test_input_roster_keeps_original_plan_owner_after_history_correction(client_a_admin, m4_seed, app_rw_session):
    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w)
    history = (await api.get('/api/ownership', params={'unit_id': w['u2']})).json()[0]
    response = await api.patch(f"/api/ownership/{history['id']}", json={'valid_to': '2024-12-31'})
    assert response.status_code == 200, response.text
    response = await api.post('/api/weg-settlements', json={'property_id': w['property'],
        'economic_plan_id': plan, 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    derived = await api.post(f'/api/weg-settlements/{run}/paid-advances-derived')
    assert derived.status_code == 200, derived.text
    roster = await api.get(f'/api/weg-settlements/{run}/input-owners')
    assert roster.status_code == 200, roster.text
    assert set(roster.json()) == {row['owner_profile_id'] for row in derived.json()} == {w['owner_a'], w['owner_b']}
    assert all(row['paid_total'] == '0.00' for row in derived.json())
    from app.repositories.coverage_declaration_repo import CoverageDeclarationRepository
    from app.repositories.economic_plan_repo import EconomicPlanRepository

    org = uuid.UUID(str(w['org_a_id']))
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(org)})
    original = await EconomicPlanRepository(app_rw_session, org).get_by_id(uuid.UUID(plan))
    assert await CoverageDeclarationRepository(app_rw_session, org).owner_ids(original) == {w['owner_a'], w['owner_b']}


@pytest.mark.parametrize('payload_version', [None, 'm4.2.0', 'm4.3.0', 'm4.3.2', 'm4.4.0'])
def test_pdf_reserve_payment_source_uses_only_the_frozen_engine_version(payload_version):
    from types import SimpleNamespace
    from app.services.weg_jahresabrechnung_pdf import build_weg_jahresabrechnung_html

    owner_id = uuid.UUID(int=1)
    payload = {'run_type': 'WEG_JAHRESABRECHNUNG',
        'period': {'start': '2025-01-01', 'end': '2025-12-31'},
        'result': {'owners': [{'owner_profile_id': str(owner_id), 'owner_cost_total': '4200.00',
            'abrechnungsspitze': '0.00', 'plan_soll': '7800.00', 'paid': '7800.00',
            'account_balance': '0.00', 'payment_amount': '0.00'}]}}
    if payload_version is not None:
        payload['engine_version'] = payload_version
    # Deliberately disagree: the frozen payload governs historical rendering.
    run = SimpleNamespace(snapshot_payload=payload, snapshot_sha256=None,
        engine_version='m4.3.2' if payload_version == 'm4.4.0' else 'm4.4.0',
        period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
    line = SimpleNamespace(addressee_owner_profile_id=owner_id, line_type='OWNER_COST_TOTAL',
        label='Summe anteilige Kosten (Ist)', amount=Decimal('4200.00'), sort_order=1)
    html = build_weg_jahresabrechnung_html(run, [line])
    sentence = ('Gesondert gebuchte Rücklagenzahlungen zählen nur, soweit das Hausgeld-Soll nicht bereits '
                'durch Hausgeldzahlungen gedeckt ist; ein Überschuss bleibt in der Rücklage.')
    assert (sentence in html) == (payload_version == 'm4.4.0')
    if payload_version != 'm4.4.0':
        assert '(Periodenzuordnung, einschließlich Erstattungen). Offene oder ungebuchte Zahlungen' in html


@pytest.mark.asyncio
@pytest.mark.parametrize('hausgeld,reserve_in,reserve_out,reverse,paid,balance', [
    ('7800.00', '1000.00', '0.00', False, '7800.00', '0.00'),
    ('7000.00', '1500.00', '0.00', False, '7800.00', '0.00'),
    ('4200.00', '3600.00', '600.00', False, '7200.00', '600.00'),
    ('7800.00', '0.00', '500.00', False, '7800.00', '0.00'),
    ('8000.00', '500.00', '0.00', False, '8000.00', '-200.00'),
    ('4200.00', '3600.00', '0.00', True, '4200.00', '3600.00'),
], ids=['full-hausgeld', 'partial-hausgeld', 'reserve-refund', 'negative-reserve', 'hausgeld-credit', 'reserve-storno'])
async def test_separate_reserve_only_covers_unpaid_plan_soll(
    client_a_admin, m4_seed, hausgeld, reserve_in, reserve_out, reverse, paid, balance,
):
    from tests.test_reserve_ledger import payment

    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w)
    await payment(api, w, hausgeld, kind='HAUSGELD_VORSCHUSS')
    if Decimal(reserve_in):
        identifier = await payment(api, w, reserve_in)
        if reverse:
            response = await api.post(f'/api/payments/{identifier}/reverse', json={'note': 'Synthetic duplicate'})
            assert response.status_code == 201, response.text
    if Decimal(reserve_out):
        response = await api.post('/api/payments', json={
            'bank_account_id': w['bank_giro'], 'owner_profile_id': w['owner_a'],
            'direction': 'OUT', 'amount': reserve_out, 'value_date': '2025-02-01',
            'payment_type': 'RUECKLAGENANTEIL', 'period_year': 2025, 'period_month': 2})
        assert response.status_code == 201, response.text
        booked = await api.post(f"/api/payments/{response.json()['id']}/book")
        assert booked.status_code == 200, booked.text
    response = await api.post('/api/weg-settlements', json={
        'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    result = (await finalize_weg_fixture(api, response.json()['id'], w)).json()
    owner = next(o for o in result['owner_results'] if o['owner_profile_id'] == w['owner_a'])
    assert (owner['paid'], owner['account_balance'], owner['payment_amount']) == (paid, balance, balance)


@pytest.mark.asyncio
async def test_separate_reserve_without_plan_soll_cannot_cover_negative_hausgeld(client_a_admin, m4_seed):
    from tests.test_reserve_ledger import payment

    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w)
    old = (await api.get('/api/ownership', params={'unit_id': w['u1']})).json()[0]
    response = await api.patch(f"/api/ownership/{old['id']}", json={'valid_to': '2025-06-30'})
    assert response.status_code == 200, response.text
    person = await api.post('/api/persons', json={'first_name': 'Clara', 'last_name': 'Successor'})
    assert person.status_code == 201, person.text
    successor = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
    assert successor.status_code == 201, successor.text
    oid = successor.json()['id']
    response = await api.post('/api/ownership', json={'unit_id': w['u1'], 'owner_profile_id': oid,
        'valid_from': '2025-07-01'})
    assert response.status_code == 201, response.text
    await payment(api, {**w, 'owner_a': oid}, '100.00', kind='HAUSGELD_VORSCHUSS', direction='OUT', when='2025-08-01')
    await payment(api, {**w, 'owner_a': oid}, '500.00', when='2025-08-01')
    response = await api.post('/api/weg-settlements', json={
        'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    derived = await api.post(f'/api/weg-settlements/{run}/paid-advances-derived')
    assert derived.status_code == 200, derived.text
    values = {'paid_advances': derived.json()}
    await attest_fixture(api, 'settlement', run, values)
    result = await api.post(f'/api/weg-settlements/{run}/finalize', json=values)
    assert result.status_code == 200, result.text
    owner = next(o for o in result.json()['owner_results'] if o['owner_profile_id'] == oid)
    assert (owner['account_balance'], owner['payment_amount']) == ('100.00', '2217.26')
    assert next(o['paid_total'] for o in derived.json() if o['owner_profile_id'] == oid) == '-100.00'


@pytest.mark.asyncio
async def test_separate_reserve_cap_sums_the_owners_two_unit_advances(client_a_admin, m4_seed):
    from tests.test_reserve_ledger import payment

    api, w = client_a_admin, m4_seed
    old = (await api.get('/api/ownership', params={'unit_id': w['u2']})).json()[0]
    response = await api.patch(f"/api/ownership/{old['id']}", json={'valid_to': '2024-12-31'})
    assert response.status_code == 200, response.text
    response = await api.post('/api/ownership', json={'unit_id': w['u2'],
        'owner_profile_id': w['owner_a'], 'valid_from': '2025-01-01'})
    assert response.status_code == 201, response.text
    plan = await _create_and_finalize_plan(api, w)
    await payment(api, w, '7000.00', kind='HAUSGELD_VORSCHUSS')
    await payment(api, w, '6500.00')
    response = await api.post('/api/weg-settlements', json={
        'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    run = response.json()['id']
    values = {'paid_advances': [{'owner_profile_id': w['owner_a'], 'paid_total': '13000.00'}]}
    await attest_fixture(api, 'settlement', run, values)
    response = await api.post(f'/api/weg-settlements/{run}/finalize', json=values)
    assert response.status_code == 200, response.text
    result = response.json()
    owner = next(o for o in result['owner_results'] if o['owner_profile_id'] == w['owner_a'])
    assert (owner['plan_soll'], owner['paid'], owner['account_balance'], owner['payment_amount']) == (
        '13000.00', '13000.00', '0.00', '0.00')


@pytest.mark.asyncio
async def test_separately_paid_reserve_settles_the_complete_plan(client_a_admin, m4_seed):
    from tests.test_reserve_ledger import payment

    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w)
    for key, costs, reserve in [('owner_a', '4200.00', '3600.00'), ('owner_b', '2800.00', '2400.00')]:
        owner_world = {**w, 'owner_a': w[key]}
        await payment(api, owner_world, costs, kind='HAUSGELD_VORSCHUSS')
        await payment(api, owner_world, reserve)
    # A reversed reserve receipt contributes zero to the signed payment total.
    reversed_id = await payment(api, w, '100.00')
    reversed_receipt = await api.post(f'/api/payments/{reversed_id}/reverse', json={'note': 'Synthetic duplicate'})
    assert reversed_receipt.status_code == 201, reversed_receipt.text
    response = await api.post('/api/weg-settlements', json={
        'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    result = (await finalize_weg_fixture(api, response.json()['id'], w)).json()
    assert len(result['owner_results']) == 2
    for owner in result['owner_results']:
        assert (owner['abrechnungsspitze'], owner['account_balance'], owner['payment_amount']) == (
            '0.00', '0.00', '0.00')


@pytest.mark.asyncio
@pytest.mark.parametrize('prior_plan_reserve', [None, '6000.00'])
async def test_settlement_reserve_soll_uses_linked_plan_after_frozen_report(
    client_a_admin, m4_seed, prior_plan_reserve,
):
    api, w = client_a_admin, m4_seed
    plan = await _create_and_finalize_plan(api, w) if prior_plan_reserve else None
    report = await api.post('/api/weg-settlements/asset-reports', json={
        'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert report.status_code == 201, report.text
    report_id = report.json()['id']
    values = {'bank_balances': [
        {'bank_account_id': w[key], 'label': key, 'account_type': kind, 'balance': '0.00'}
        for key, kind in [('bank_giro', 'LAUFEND'), ('bank_ruecklage', 'RUECKLAGE')]],
        'receivables': [], 'payables': []}
    await attest_fixture(api, 'settlement', report_id, values)
    frozen = await api.post(f'/api/weg-settlements/asset-reports/{report_id}/finalize', json=values)
    assert frozen.status_code == 200, frozen.text
    if plan:
        correction = await api.post(f'/api/economic-plans/{plan}/correction')
        assert correction.status_code == 201, correction.text
        plan = correction.json()['id']
        values = {'planned_costs': [
            {'category_code': 'GARTENPFLEGE', 'label': 'Gartenpflege', 'planned_amount': '4000.00', 'cost_group': 'UMLAGEFAEHIG'},
            {'category_code': 'WEG_VERWALTERKOSTEN', 'label': 'Verwalter', 'planned_amount': '3000.00', 'cost_group': 'NICHT_UMLAGEFAEHIG'}],
            'reserve_contribution_total': '8000.00'}
        await attest_fixture(api, 'economic_plan', plan, values)
        finalized = await api.post(f'/api/economic-plans/{plan}/finalize', json=values)
        assert finalized.status_code == 200, finalized.text
        expected = {w['owner_a']: '4800.00', w['owner_b']: '3200.00'}
    else:
        plan = await _create_and_finalize_plan(api, w)
        expected = {w['owner_a']: '3600.00', w['owner_b']: '2400.00'}
    response = await api.post('/api/weg-settlements', json={
        'property_id': w['property'], 'economic_plan_id': plan,
        'period_start': '2025-01-01', 'period_end': '2025-12-31'})
    assert response.status_code == 201, response.text
    result = (await finalize_weg_fixture(api, response.json()['id'], w)).json()
    assert {o['owner_profile_id']: o['abrechnungsspitze'] for o in result['owner_results']} == {
        w['owner_a']: '0.00', w['owner_b']: '0.00'}
    assert {ln['addressee_owner_profile_id']: ln['amount'] for ln in result['lines']
            if ln['line_type'] == 'RESERVE_SOLL'} == expected
    reloaded = await api.get(f'/api/weg-settlements/{report_id}')
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json() == frozen.json()


def test_planned_reserve_sums_the_ledger_planned_rows_per_owner():
    from app.engine.types import OwnerAdvanceInput
    from app.services.weg_settlement_service import WegSettlementService

    owner_a, owner_b = uuid.UUID(int=1), uuid.UUID(int=2)
    unit_1, unit_2 = uuid.UUID(int=11), uuid.UUID(int=12)

    def planned(unit, owner, month, amount):
        return {'kind': 'PLANNED', 'date': f'2025-{month:02d}-01', 'unit_id': str(unit),
                'owner_profile_id': str(owner), 'amount': amount}

    entries = [
        # Owner b is listed first: the result is ordered by owner id, not by row order.
        *[planned(unit_2, owner_b, month, '200.00') for month in range(1, 13)],
        planned(unit_1, owner_a, 1, '200.01'),
        *[planned(unit_1, owner_a, month, '200.00') for month in range(2, 13)],
        *[planned(unit_2, owner_a, month, '100.00') for month in range(1, 13)],
        {'kind': 'CONTRIBUTION', 'date': '2025-03-01', 'owner_profile_id': str(owner_a), 'amount': '500.00'},
        {'kind': 'PLANNED', 'date': '2025-04-01', 'unit_id': str(unit_1), 'amount': '999.00'},
    ]
    service = WegSettlementService.__new__(WegSettlementService)
    assert service._plan_reserve_per_owner(entries) == (
        OwnerAdvanceInput(owner_a, Decimal('3600.01')), OwnerAdvanceInput(owner_b, Decimal('2400.00')))
    assert service._plan_reserve_per_owner([]) == ()
