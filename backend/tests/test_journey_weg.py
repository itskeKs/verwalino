"""T13: Full WEG year journey — independent WEG with MEA, two owners, economic plan,
Hausgeld payments, costs, annual settlement (T09), asset report, resolution collection,
all PDFs.

Dateien: `backend/tests/test_journey_weg.py` (neu), `backend/tests/conftest.py`,
Muster `backend/tests/test_m4_weg.py`, `backend/tests/test_m8_meetings.py`

Fertig, wenn: `uv run pytest tests/test_journey_weg.py` grün UND Ablauf in der App:
"dieselben Schritte einmal klicken"."""

import decimal
import os
import uuid
from datetime import date, datetime

import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import MIGRATOR_SYNC_URL
from tests.financial_coverage_helpers import attest_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["economic_plan", "weg", "asset_report"])
async def test_weg_pdf_available_before_success_response(t13_seed, client_a_admin, kind):
    """An HTTP client may open the PDF before request dependency cleanup runs."""
    from app.main import app

    seed = t13_seed
    period = {"property_id": seed["property_id"],
              "period_start": "2025-01-01", "period_end": "2025-12-31"}
    if kind == "economic_plan":
        created = await client_a_admin.post("/api/economic-plans", json={**period, "plan_year": 2025})
        values = {"reserve_contribution_total": "1000.00", "planned_costs": [
            {"category_code": "WEG_VERWALTERKOSTEN", "label": "Verwaltung", "planned_amount": "5000.00",
             "cost_group": "NICHT_UMLAGEFAEHIG"},
        ]}
        prefix, coverage_kind = "/api/economic-plans", "economic_plan"
    elif kind == "weg":
        plan_id = await _create_and_finalize_plan(client_a_admin, seed["property_id"], seed["mandate_id"])
        created = await client_a_admin.post("/api/weg-settlements", json={**period, "economic_plan_id": plan_id})
        values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "paid_advances": [
            {"owner_profile_id": seed[owner], "paid_total": "6000.00"} for owner in ("owner_a", "owner_b")
        ]}
        prefix, coverage_kind = "/api/weg-settlements", "settlement"
    else:
        await _create_and_finalize_plan(client_a_admin, seed['property_id'], seed['mandate_id'])
        created = await client_a_admin.post("/api/weg-settlements/asset-reports", json=period)
        values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00",
                  "receivables": [], "payables": [],
                  "bank_balances": [
                      {"bank_account_id": seed[key], "label": label, "account_type": account_type, "balance": amount}
                      for key, label, account_type, amount in (
                          ("bank_giro", "Giro", "LAUFEND", "0.00"),
                          ("bank_ruecklage", "Reserve", "RUECKLAGE", "1000.00"),
                      )
                  ]}
        prefix, coverage_kind = "/api/weg-settlements/asset-reports", "settlement"
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    url = f"{prefix}/{run_id}"
    if kind != 'economic_plan':
        for field in ('reserve_contributions_total', 'reserve_withdrawals_total'):
            refused = await client_a_admin.post('/api/financial-input-coverage', json={
                'subject_kind': coverage_kind, 'subject_id': run_id, 'values': {**values, field: '1000.00'},
                'confirmed_complete': True, 'reason': 'Review derived total'})
            assert refused.status_code == 422, refused.text
            assert refused.json()['detail']['code'] == 'RESERVE_DERIVED_TOTAL_READ_ONLY'
    await attest_fixture(client_a_admin, coverage_kind, run_id, values)
    computed = await client_a_admin.post(f"{url}/compute", json=values)
    assert computed.status_code == 200, computed.text
    assert computed.json()["status"] == "draft"
    assert (await client_a_admin.get(f"{url}/pdf")).status_code == 409

    observed = []

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message["type"] == "http.response.start":
                # A separate request/transaction while finalize's dependency
                # is still active exposes the race hidden by ASGITransport.
                observed.append(await client_a_admin.get(f"{url}/pdf"))
            await send(message)
        await app(scope, receive, observe_send)

    async with AsyncClient(transport=ASGITransport(app=observe_response), base_url="http://test",
                           cookies=client_a_admin.cookies) as client:
        finalized = await client.post(f"{url}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "finalized"
    assert len(observed) == 1
    assert observed[0].status_code == 200, observed[0].text
    assert observed[0].headers["content-type"] == "application/pdf"
    assert observed[0].content.startswith(b"%PDF-")


@pytest_asyncio.fixture
async def journey_client(client_a_admin):
    """Exercise identical assertions through the explicitly selected local API."""
    if os.environ.get("HV_JOURNEY_HTTP") == "1":
        async with AsyncClient(
            base_url="http://127.0.0.1:18000", cookies=client_a_admin.cookies, timeout=60,
        ) as client:
            yield client
    else:
        yield client_a_admin


@pytest.fixture(scope="function")
def t13_seed(seed_data):
    """Independent WEG property with two owners, MEA units, Hausgeld, costs, plan, meeting.

    Uses seed_data keys: org_a_id, user_a_admin_id, mandate_a_id (existing WEG mandate).
    Creates all other IDs locally.
    """
    org = seed_data["org_a_id"]
    user_id = seed_data["user_a_admin_id"]
    mandate_id = seed_data["mandate_a_id"]  # existing WEG mandate

    conn = psycopg2.connect(MIGRATOR_SYNC_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # Generate local UUIDs
    property_id = uuid.uuid4()
    u1 = uuid.uuid4()
    u2 = uuid.uuid4()
    person_a = uuid.uuid4()
    person_b = uuid.uuid4()
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    bank_giro = uuid.uuid4()
    bank_ruecklage = uuid.uuid4()

    try:
        # Property (WEG type)
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) "
            "VALUES (%s,%s,%s,%s)",
            (str(property_id), org, "Test-WEG T13", "WEG"),
        )

        # Units with MEA (600/400)
        for uid, num, mea in [(u1, "1", 600), (u2, "2", 400)]:
            cur.execute(
                "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uid), org, str(property_id), num, "WOHNUNG", mea, mea, mea),
            )

        # Persons
        for pid, first in [(person_a, "Anton"), (person_b, "Berta")]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) "
                "VALUES (%s,%s,%s,%s)",
                (str(pid), org, first, "Eigentümer"),
            )

        # Owner profiles
        for oid, pid in [(owner_a, person_a), (owner_b, person_b)]:
            cur.execute(
                "INSERT INTO owner_profile (id, organization_id, person_id) "
                "VALUES (%s,%s,%s)",
                (str(oid), org, str(pid)),
            )
            # Ownership history (unit-based, not property-based)
            unit_id = u1 if oid == owner_a else u2
            cur.execute(
                "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), org, str(unit_id), str(oid), date(2000, 1, 1), None),
            )

        # Bank accounts (use existing mandate)
        for bid, atype, label in [
            (bank_giro, "LAUFEND", "Girokonto"),
            (bank_ruecklage, "RUECKLAGE", "Rücklagenkonto"),
        ]:
            cur.execute(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(bid), org, str(property_id), atype, label),
            )

        # 24 monthly HAUSGELD_VORSCHUSS payments (12 months × 2 owners)
        for month in range(1, 13):
            # Owner A: 500/month
            cur.execute(
                "INSERT INTO payment (id, organization_id, bank_account_id, property_id, "
                "management_mandate_id, direction, amount, value_date, payment_type, "
                "owner_profile_id, lease_agreement_id, period_year, period_month, status, "
                "booked_at, booked_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid5(uuid.UUID(str(property_id)), f"p_a_{month}")),
                    org, str(bank_giro), str(property_id), str(mandate_id),
                    "IN", decimal.Decimal("500.00"), date(2025, month, 1),
                    "HAUSGELD_VORSCHUSS", str(owner_a), None,
                    2025, month, "booked",
                    datetime.now(), str(user_id),
                ),
            )
            # Owner B: 500/month
            cur.execute(
                "INSERT INTO payment (id, organization_id, bank_account_id, property_id, "
                "management_mandate_id, direction, amount, value_date, payment_type, "
                "owner_profile_id, lease_agreement_id, period_year, period_month, status, "
                "booked_at, booked_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid5(uuid.UUID(str(property_id)), f"p_b_{month}")),
                    org, str(bank_giro), str(property_id), str(mandate_id),
                    "IN", decimal.Decimal("500.00"), date(2025, month, 1),
                    "HAUSGELD_VORSCHUSS", str(owner_b), None,
                    2025, month, "booked",
                    datetime.now(), str(user_id),
                ),
            )

        # Cost entries (3 categories, shared across property)
        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        garten = cat("GARTENPFLEGE")
        verwaltung = cat("WEG_VERWALTERKOSTEN")
        ruecklage = cat("ERHALTUNGSRUECKLAGE")

        for code, gross, cat_id in [
            ("GARTENPFLEGE", decimal.Decimal("4000.00"), garten),
            ("WEG_VERWALTERKOSTEN", decimal.Decimal("3000.00"), verwaltung),
            ("ERHALTUNGSRUECKLAGE", decimal.Decimal("6000.00"), ruecklage),
        ]:
            ce = uuid.uuid4()
            cur.execute(
                "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, "
                "cost_category_plan_version, invoice_date, service_period_from, service_period_to, "
                "gross_amount, owner_distribution_key, tenant_distribution_key, tenant_apportionable, "
                "status, source, confirmed_at, confirmed_by) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
                (
                    str(ce), org, str(property_id), str(cat_id),
                    date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                    gross, "MEA", "MEA", "NICHT_UMLAGEFAEHIG",
                    str(user_id),
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        **seed_data,
        "property_id": str(property_id),
        "u1": str(u1),
        "u2": str(u2),
        "owner_a": str(owner_a),
        "owner_b": str(owner_b),
        "bank_giro": str(bank_giro),
        "bank_ruecklage": str(bank_ruecklage),
        "mandate_id": str(seed_data["mandate_a_id"]),
    }


async def _create_and_finalize_plan(client, property_id, mandate_id, reserve="6000.00"):
    """Create + finalize a Wirtschaftsplan with the two owners' MEA shares."""
    resp = await client.post("/api/economic-plans", json={
        "property_id": property_id,
        "plan_year": 2025,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "management_mandate_id": str(mandate_id),
    })
    assert resp.status_code == 201, resp.text
    plan_id = resp.json()["id"]
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
async def test_full_weg_journey(t13_seed, journey_client: AsyncClient):
    client_a_admin = journey_client
    """Full WEG journey: economic plan, Hausgeld, costs, annual settlement (T09), asset report, resolution collection."""
    seed = t13_seed
    org = seed["org_a_id"]
    property_id = seed["property_id"]
    owner_a = seed["owner_a"]
    owner_b = seed["owner_b"]
    mandate_id = seed["mandate_a_id"]
    bank_giro = seed["bank_giro"]

    # 1. Verify MEA scale on units
    resp = await client_a_admin.get(f"/api/units?property_id={property_id}")
    assert resp.status_code == 200, resp.text
    units = resp.json()
    assert len(units) == 2
    unit_1_mea = None
    unit_2_mea = None
    for u in units:
        if u["number"] == "1":
            unit_1_mea = u["mea"]
        elif u["number"] == "2":
            unit_2_mea = u["mea"]
    assert float(unit_1_mea) == 600.0
    assert float(unit_2_mea) == 400.0

    # 2. Verify 24 payments (12 months × 2 owners) using bank_account_id
    resp = await client_a_admin.get(
        f"/api/payments?bank_account_id={bank_giro}&payment_type=HAUSGELD_VORSCHUSS"
    )
    assert resp.status_code == 200, resp.text
    payments = resp.json()
    assert len(payments) == 24

    # Verify total paid: 24 × 500 = 12000
    total_paid = sum(decimal.Decimal(p["amount"]) for p in payments)
    assert total_paid == decimal.Decimal("12000.00")

    # 3. Verify costs (3 categories) using correct route /api/cost-entries
    resp = await client_a_admin.get(f"/api/cost-entries?property_id={property_id}")
    assert resp.status_code == 200, resp.text
    costs = resp.json()
    assert len(costs) == 3

    # 4. Create and finalize economic plan
    plan_id = await _create_and_finalize_plan(client_a_admin, property_id, mandate_id)

    # 5. Create WEG settlement with economic_plan_id
    resp = await client_a_admin.post(
        "/api/weg-settlements",
        json={
            "property_id": property_id,
            "economic_plan_id": plan_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        },
    )
    assert resp.status_code == 201, resp.text
    settlement = resp.json()
    settlement_id = settlement["id"]

    # 6. Derive paid advances (T09) using settlement run_id
    resp = await client_a_admin.post(
        f"/api/weg-settlements/{settlement_id}/paid-advances-derived",
        json={"year": 2025},
    )
    assert resp.status_code == 200, resp.text
    derived = resp.json()
    # Each owner paid 12 × 500 = 6000
    assert len(derived) == 2  # Two owner profiles
    # Find each owner's paid total
    owner_totals = {}
    for item in derived:
        oid = item["owner_profile_id"]
        owner_totals[oid] = decimal.Decimal(item["paid_total"])
    assert owner_totals[owner_a] == decimal.Decimal("6000.00")
    assert owner_totals[owner_b] == decimal.Decimal("6000.00")

    # 7. Attest settlement coverage, then finalize with actual derived paid advances
    paid_advances = [{"owner_profile_id": item["owner_profile_id"], "paid_total": item["paid_total"]} for item in derived]
    values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00",
              "paid_advances": paid_advances}
    await attest_fixture(client_a_admin, "settlement", settlement_id, values)
    resp = await client_a_admin.post(
        f"/api/weg-settlements/{settlement_id}/finalize",
        json=values,
    )
    assert resp.status_code == 200, resp.text

    # 8. Annual settlement PDF
    resp = await client_a_admin.get(
        f"/api/weg-settlements/{settlement_id}/pdf"
    )
    assert resp.status_code == 200, resp.text
    assert "application/pdf" in resp.headers.get("content-type", "")

    # 9. Economic plan PDF
    resp = await client_a_admin.get(
        f"/api/economic-plans/{plan_id}/pdf"
    )
    assert resp.status_code == 200, resp.text

    # Asset report uses the same year and the fixture's two bank accounts.
    resp = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
        "property_id": property_id,
        "period_start": "2025-01-01", "period_end": "2025-12-31",
    })
    assert resp.status_code == 201, resp.text
    report_id = resp.json()["id"]
    report_values = {
        "receivables": [], "payables": [],
        "bank_balances": [
            {"bank_account_id": bank_giro, "label": "Giro", "account_type": "LAUFEND", "balance": "0.00"},
            {"bank_account_id": seed["bank_ruecklage"], "label": "Reserve", "account_type": "RUECKLAGE", "balance": "1000.00"},
        ],
    }
    await attest_fixture(client_a_admin, "settlement", report_id, report_values)
    resp = await client_a_admin.post(
        f"/api/weg-settlements/asset-reports/{report_id}/finalize", json=report_values,
    )
    assert resp.status_code == 200, resp.text
    resp = await client_a_admin.get(f"/api/weg-settlements/asset-reports/{report_id}/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF")

    resp = await client_a_admin.post("/api/meetings", json={
        "property_id": property_id, "meeting_date": "2025-12-15",
        "meeting_type": "ORDENTLICH", "location": "Sitzungsraum 1",
    })
    assert resp.status_code == 201, resp.text
    meeting_id = resp.json()["id"]
    resp = await client_a_admin.post(f"/api/meetings/{meeting_id}/agenda", json={
        "meeting_id": meeting_id, "title": "Jahresabrechnung 2025",
    })
    assert resp.status_code == 201, resp.text
    agenda_id = resp.json()["id"]
    for number, wording in enumerate((
        "Jahresabrechnung 2025 wird genehmigt", "Nächste Sitzung im Januar 2026",
    ), start=1):
        resp = await client_a_admin.post("/api/meetings/resolutions", json={
            "meeting_id": meeting_id, "agenda_item_id": agenda_id,
            "wording": wording, "votes_yes": 2, "votes_no": 0, "votes_abstain": 0,
            "result": "ANGENOMMEN", "majority_type": "einfache Mehrheit",
        })
        assert resp.status_code == 201, resp.text
        resolution_id = resp.json()["id"]
        resp = await client_a_admin.post(f"/api/meetings/resolutions/{resolution_id}/register")
        assert resp.status_code == 201, resp.text
        assert resp.json()["sequence_number"] == number
    resp = await client_a_admin.get(f"/api/meetings/register/{property_id}")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2
    for url in (f"/api/meetings/{meeting_id}/pdf", f"/api/meetings/register/{property_id}/pdf"):
        resp = await client_a_admin.get(url)
        assert resp.status_code == 200, resp.text
        assert resp.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_pdf_generation_only(t13_seed, journey_client: AsyncClient):
    client_a_admin = journey_client
    """Verify all PDF endpoints return 200."""
    seed = t13_seed
    property_id = seed["property_id"]
    mandate_id = seed["mandate_a_id"]

    # Create and finalize economic plan
    plan_id = await _create_and_finalize_plan(client_a_admin, property_id, mandate_id)

    # Create settlement
    resp = await client_a_admin.post(
        "/api/weg-settlements",
        json={
            "property_id": property_id,
            "economic_plan_id": plan_id,
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        },
    )
    assert resp.status_code == 201, resp.text
    settlement_id = resp.json()["id"]

    # Derive paid advances from 24 payments (500 each, 12 per owner)
    resp = await client_a_admin.post(
        f"/api/weg-settlements/{settlement_id}/paid-advances-derived",
        json={"year": 2025},
    )
    assert resp.status_code == 200, resp.text
    derived = resp.json()
    paid_advances = [{"owner_profile_id": item["owner_profile_id"], "paid_total": item["paid_total"]} for item in derived]

    # Attest settlement coverage before compute/finalize
    values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00",
              "paid_advances": paid_advances}
    await attest_fixture(client_a_admin, "settlement", settlement_id, values)

    # Compute settlement with derived paid advances
    resp = await client_a_admin.post(
        f"/api/weg-settlements/{settlement_id}/compute",
        json={
            "reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00",
            "paid_advances": paid_advances,
        },
    )
    assert resp.status_code == 200, resp.text

    # Finalize settlement
    resp = await client_a_admin.post(
        f"/api/weg-settlements/{settlement_id}/finalize",
        json=values,
    )
    assert resp.status_code == 200, resp.text

    # Annual settlement PDF
    resp = await client_a_admin.get(
        f"/api/weg-settlements/{settlement_id}/pdf"
    )
    assert resp.status_code == 200, resp.text

    # Economic plan PDF
    resp = await client_a_admin.get(
        f"/api/economic-plans/{plan_id}/pdf"
    )
    assert resp.status_code == 200, resp.text
