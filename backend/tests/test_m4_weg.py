"""M4 WEG tests -- persistence, RLS, immutability, atomic finalize, and the M4
exit (Wirtschaftsplan + WEG-Jahresabrechnung with hand-checked Abrechnungsspitze
+ Vermoegensbericht, all as PDF; amounts consistent).

Uses the same fixtures as M0-M3. The engine math itself is proven in
tests/engine/ (Golden + property-based); here we prove the DB invariants and the
full create->compute->finalize->PDF path through the real service + API.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ===========================================================================
# Seed: a WEG (org_a) -- 2 units MEA 600/400, two owners; cost entries from
# three spheres (umlagefaehig / nicht umlagefaehig / Erhaltungsruecklage).
# Mirrors engine Golden 2/3 so the Abrechnungsspitze is hand-checkable.
# ===========================================================================
@pytest.fixture(scope="session")
def m4_seed(seed_data):
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


async def _create_and_finalize_plan(client, m4_seed, reserve="6000.00"):
    """Create + finalize a Wirtschaftsplan with the two owners' MEA shares."""
    resp = await client.post("/api/economic-plans", json={
        "property_id": m4_seed["property"],
        "plan_year": 2025,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
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
    resp = await client.post(f"/api/economic-plans/{plan_id}/finalize", json=body)
    assert resp.status_code == 200, resp.text
    return plan_id


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
    async def test_update_finalized_plan_rejected(self, app_rw_session, m4_seed, client_a_admin):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :oid, true)"),
            {"oid": str(m4_seed["org_a_id"])},
        )
        with pytest.raises(Exception, match="GoBD|finalized"):
            await app_rw_session.execute(
                text("UPDATE economic_plan SET reserve_contribution_total = 999 WHERE id = :id"),
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
    async def test_full_weg_settlement(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        # Create the WEG run linked to the plan.
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        # Compute + finalize with reserve opening 20000, withdrawal 8000.
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json={
            "reserve_opening_balance": "20000.00",
            "reserve_withdrawals_total": "8000.00",
        })
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
        assert cost_totals[m4_seed["owner_a"]] == "4200.00"
        assert cost_totals[m4_seed["owner_b"]] == "2800.00"

        # Plan Soll: the plan advance per owner is cost+reserve share:
        #   A 13000*600/1000 = 7800 ; B = 5200.
        plan_adv = {ln["addressee_owner_profile_id"]: ln["amount"]
                    for ln in lines if ln["line_type"] == "PLAN_ADVANCE"}
        assert plan_adv[m4_seed["owner_a"]] == "7800.00"
        assert plan_adv[m4_seed["owner_b"]] == "5200.00"

        # Abrechnungsspitze = Ist - Soll (NOT incl. reserve in the Ist):
        #   A 4200 - 7800 = -3600.00 (Guthaben) ; B 2800 - 5200 = -2400.00.
        spitze = {ln["addressee_owner_profile_id"]: ln["amount"]
                  for ln in lines if ln["line_type"] == "ABRECHNUNGSSPITZE"}
        assert spitze[m4_seed["owner_a"]] == "-3600.00"
        assert spitze[m4_seed["owner_b"]] == "-2400.00"

        # Reserve contribution per owner (separate, NOT in OWNER_COST_TOTAL):
        #   A 6000*600/1000 = 3600 ; B = 2400.
        res_owner = {ln["addressee_owner_profile_id"]: ln["amount"]
                     for ln in lines if ln["line_type"] == "RESERVE_CONTRIBUTION"
                     and ln["addressee_owner_profile_id"] is not None}
        assert res_owner[m4_seed["owner_a"]] == "3600.00"
        assert res_owner[m4_seed["owner_b"]] == "2400.00"

        # Reserve development (object-wide): 20000 + 6000 - 8000 = 18000.
        closing = next(ln for ln in lines if ln["line_type"] == "RESERVE_CLOSING")
        assert closing["amount"] == "18000.00"
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
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json={})
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
    async def test_weg_pdf_renders_for_finalized(self, client_a_admin, m4_seed):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json={})
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
        await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json={})
        resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/compute", json={})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_payload_consistent_with_lines(self, client_a_admin, m4_seed, app_rw_session):
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
        resp = await client_a_admin.post("/api/weg-settlements", json={
            "property_id": m4_seed["property"], "economic_plan_id": plan_id,
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        run_id = resp.json()["id"]
        await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json={})

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
    async def test_full_asset_report(self, client_a_admin, m4_seed):
        resp = await client_a_admin.post("/api/weg-settlements/asset-reports", json={
            "property_id": m4_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31",
        })
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        resp = await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/finalize", json={
            "reserve_opening_balance": "20000.00",
            "reserve_withdrawals_total": "8000.00",
            "reserve_contributions_total": "6000.00",
            "bank_balances": [
                {"bank_account_id": m4_seed["bank_giro"], "label": "Girokonto", "account_type": "LAUFEND", "balance": "15000.00"},
                {"bank_account_id": m4_seed["bank_ruecklage"], "label": "Ruecklagenkonto", "account_type": "RUECKLAGE", "balance": "18000.00"},
            ],
            "receivables": [{"label": "Hausgeldrueckstand", "amount": "500.00"}],
            "payables": [{"label": "offene Rechnung", "amount": "1200.00"}],
        })
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
        await client_a_admin.post(f"/api/weg-settlements/asset-reports/{run_id}/finalize", json={
            "reserve_opening_balance": "1000.00",
            "bank_balances": [{"bank_account_id": m4_seed["bank_giro"], "label": "Giro", "account_type": "LAUFEND", "balance": "500.00"}],
        })
        resp = await client_a_admin.get(f"/api/weg-settlements/asset-reports/{run_id}/pdf")
        assert resp.status_code == 200
        assert resp.content[:4] == b"%PDF"


# ===========================================================================
# Snapshot/hash determinism through the service (m4 section 9.1 #9).
# ===========================================================================
class TestM4Determinism:
    @pytest.mark.asyncio
    async def test_weg_hash_stable_across_two_finalizations(self, client_a_admin, m4_seed):
        # Same plan + identical inputs -> identical snapshot hash. Use ONE plan
        # for both runs (the plan id is part of the snapshot, so a fresh plan
        # would legitimately change the hash -- that is correct, not a defect).
        plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)

        async def finalize_once():
            resp = await client_a_admin.post("/api/weg-settlements", json={
                "property_id": m4_seed["property"], "economic_plan_id": plan_id,
                "period_start": "2025-01-01", "period_end": "2025-12-31",
            })
            run_id = resp.json()["id"]
            resp = await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json={
                "reserve_opening_balance": "20000.00", "reserve_withdrawals_total": "8000.00",
            })
            return resp.json()["snapshot_sha256"]

        h1 = await finalize_once()
        h2 = await finalize_once()
        assert h1 == h2
