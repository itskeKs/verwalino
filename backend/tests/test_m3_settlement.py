"""M3 settlement tests -- persistence, RLS, immutability, atomic finalize, and
the end-to-end M3 exit (Mischobjekt with Mieterwechsel -> nachrechenbar correct,
formell complete BK-Abrechnung PDF).

Uses the same fixtures as M0/M1/M2 (seed_data, app_rw_session, migrator_session,
client_a_admin/clerk/readonly, client_b_admin).

The engine math itself is proven in tests/engine/ (Golden + property-based);
here we prove the DB invariants and the full create->compute->finalize->PDF path.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

from tests.financial_coverage_helpers import attest_fixture
import pytest
from sqlalchemy import text

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


# ===========================================================================
# Seed: a full Mischobjekt for org_a (via migrator -> persists across the
# API client's own transactions). Mirrors engine Golden 5 (the exit scenario).
# ===========================================================================
def _seed_m3(seed_data):
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "u2": uuid.uuid4(),
        "u3": uuid.uuid4(),
        "u4": uuid.uuid4(),
        "person_owner": uuid.uuid4(),
        "owner_profile": uuid.uuid4(),
        "tp_a": uuid.uuid4(),
        "tp_b": uuid.uuid4(),
        "tp_c": uuid.uuid4(),
        "tp_d": uuid.uuid4(),
        "tp_e": uuid.uuid4(),
        "lease_a": uuid.uuid4(),
        "lease_b": uuid.uuid4(),
        "lease_c": uuid.uuid4(),
        "lease_d": uuid.uuid4(),
        "lease_e": uuid.uuid4(),
    }
    person_ids = {k: uuid.uuid4() for k in ("a", "b", "c", "d", "e")}

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        # Property (Mischobjekt)
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "Mischhaus M3", "MISCH"),
        )
        # Units: U1 area 80/mea 300, U2 area 60/mea 250, U3 area 50/mea 200, U4 GEWERBE area 100/mea 250
        units = [
            (ids["u1"], "1", "WOHNUNG", 80, 300),
            (ids["u2"], "2", "WOHNUNG", 60, 250),
            (ids["u3"], "3", "WOHNUNG", 50, 200),
            (ids["u4"], "4", "GEWERBE", 100, 250),
        ]
        for uid, num, usage, area, mea in units:
            cur.execute(
                "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uid), org, str(ids["property"]), num, usage, area, area, mea),
            )

        # Owner person + profile; owns all units the whole time.
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_owner"]), org, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_profile"]), org, str(ids["person_owner"])),
        )
        for uid in (ids["u1"], ids["u2"], ids["u3"], ids["u4"]):
            cur.execute(
                "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), org, str(uid), str(ids["owner_profile"]), date(2000, 1, 1), None),
            )

        # Tenant persons + profiles.
        for key, first in [("a", "Anna"), ("b", "Bert"), ("c", "Cara"), ("d", "Dirk"), ("e", "Emil")]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(person_ids[key]), org, first, key.upper()),
            )
            cur.execute(
                "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(ids[f"tp_{key}"]), org, str(person_ids[key])),
            )

        # Leases: U1 A(Jan-Jun)/B(Jul-Dec); U3 C(Jan-Mar)/vacancy/D(Aug-Dec); U4 E(all year).
        #
        # valid_to is stored in the TRUE M1 half-open [) convention: it is the first
        # NON-occupied day (the EXCLUDE constraint and the point-in-time query treat
        # it as an exclusive upper bound). The repository read-edge converts it back
        # to the engine's tag-inclusive last-occupied-day (valid_to - 1 day; ADR-0004
        # #6 / m3 section 3.4). So:
        #   A last day 30.06. -> stored 2025-07-01 ; B is back-to-back from 01.07.
        #     (these two are ADJACENT and non-overlapping in the DB -- exactly the
        #      Auszug-30.06./Einzug-01.07. case that triggered the live 500 before
        #      the read-edge conversion was applied).
        #   C last day 31.03. -> stored 2025-04-01 ; D starts 01.08. -> Apr-Jul vacancy.
        #   open-ended leases (last day = settlement period end) -> NULL.
        # The resulting engine day-counts are 181 (A) / 184 (B) / 90 (C) / 153 (D),
        # matching engine Golden 5.
        leases = [
            (ids["lease_a"], ids["u1"], ids["tp_a"], date(2025, 1, 1), date(2025, 7, 1)),
            (ids["lease_b"], ids["u1"], ids["tp_b"], date(2025, 7, 1), None),
            (ids["lease_c"], ids["u3"], ids["tp_c"], date(2025, 1, 1), date(2025, 4, 1)),
            (ids["lease_d"], ids["u3"], ids["tp_d"], date(2025, 8, 1), None),
            (ids["lease_e"], ids["u4"], ids["tp_e"], date(2025, 1, 1), None),
        ]
        for lid, uid, tp, vf, vt in leases:
            cur.execute(
                "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, valid_from, valid_to, base_rent, advance_payment) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(lid), org, str(uid), str(tp), vf, vt, Decimal("800.00"), Decimal("100.00")),
            )

        # Confirmed cost entries (active chain) for 2025.
        def add_cost(code, gross, key, appo, appo_amount=None):
            ce = uuid.uuid4()
            cur.execute(
                "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
                "invoice_date, service_period_from, service_period_to, gross_amount, tenant_distribution_key, "
                "tenant_apportionable, apportionable_tenant_amount, status, source, confirmed_at, confirmed_by) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
                (
                    str(ce), org, str(ids["property"]), str(cat(code)),
                    date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                    gross, key, appo, appo_amount, str(seed_data["user_a_admin_id"]),
                ),
            )
            return ce

        ids["ce_grundsteuer"] = add_cost("GRUNDSTEUER", Decimal("2900.00"), "WOHNFLAECHE", "UMLAGEFAEHIG")
        ids["ce_muell"] = add_cost("MUELLBESEITIGUNG", Decimal("480.00"), "EINHEIT", "UMLAGEFAEHIG")
        ids["ce_wasser"] = add_cost("KALTWASSER", Decimal("600.00"), "VERBRAUCH", "UMLAGEFAEHIG")
        ids["ce_heizung"] = add_cost("HEIZUNG_ZENTRAL", Decimal("950.00"), "EXTERNE_ABRECHNUNG", "UMLAGEFAEHIG_EXTERN_PRUEFEN")
        ids["ce_co2"] = add_cost("CO2_KOSTEN", Decimal("500.00"), "WOHNFLAECHE", "TEILWEISE_PRUEFEN", Decimal("350.00"))
        ids["ce_hausmeister"] = add_cost("HAUSMEISTER", Decimal("1000.00"), "WOHNFLAECHE", "GEMISCHT_PRUEFEN", Decimal("720.00"))
        ids["ce_verwalter"] = add_cost("WEG_VERWALTERKOSTEN", Decimal("1200.00"), "MEA", "NICHT_UMLAGEFAEHIG")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


@pytest.fixture(scope="session")
def m3_seed(seed_data):
    return _seed_m3(seed_data)


def _extras_for_exit(m3_seed):
    """The structured external/consumption inputs for the exit scenario."""
    return {
        "external_amounts": [
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u1"], "tenant_amount": "210.00", "period_from": "2025-01-01", "period_to": "2025-06-30"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u1"], "tenant_amount": "190.00", "period_from": "2025-07-01", "period_to": "2025-12-31"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u3"], "tenant_amount": "100.00", "period_from": "2025-01-01", "period_to": "2025-03-31"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u3"], "tenant_amount": "150.00", "period_from": "2025-08-01", "period_to": "2025-12-31"},
            {"cost_entry_id": m3_seed["ce_heizung"], "unit_id": m3_seed["u4"], "tenant_amount": "300.00", "period_from": "2025-01-01", "period_to": "2025-12-31"},
        ],
        "unit_metrics": [
            {"unit_id": m3_seed["u1"], "consumption": "30"},
            {"unit_id": m3_seed["u2"], "consumption": "20"},
            {"unit_id": m3_seed["u3"], "consumption": "10"},
            {"unit_id": m3_seed["u4"], "consumption": "40"},
        ],
    }


async def _create_run(client, m3_seed, lease_key, advance):
    resp = await client.post("/api/settlements", json={
        "property_id": m3_seed["property"],
        "lease_agreement_id": m3_seed[lease_key],
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "advance_payments_total": advance,
    })
    assert resp.status_code == 201, resp.text
    await attest_fixture(client, "settlement", resp.json()["id"],
        {"advance_payments_total": advance, "advance_payments_source": "MANUAL"})
    return resp.json()["id"]


# ===========================================================================
# Test: RLS fail-closed + cross-org isolation for the new tables
# ===========================================================================
class TestM3RLSFailClosed:
    @pytest.mark.asyncio
    async def test_no_context_zero_runs(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM settlement_run"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_no_context_zero_lines(self, app_rw_session):
        await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        result = await app_rw_session.execute(text("SELECT * FROM settlement_line"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_bogus_uuid_zero_runs(self, app_rw_session):
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(uuid.uuid4())}
        )
        result = await app_rw_session.execute(text("SELECT * FROM settlement_run"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_insert_foreign_org_rejected(self, app_rw_session, m3_seed):
        """WITH CHECK blocks inserting a run for another org."""
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        with pytest.raises(Exception):
            await app_rw_session.execute(
                text(
                    "INSERT INTO settlement_run (id, organization_id, property_id, run_type, "
                    "period_start, period_end, advance_payments_source, status, engine_version) "
                    "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','draft','m3.1.0')"
                ),
                {"id": str(uuid.uuid4()), "org": str(m3_seed["org_b_id"]), "prop": m3_seed["property"]},
            )

    @pytest.mark.asyncio
    async def test_cross_org_runs_invisible(self, client_a_admin, client_b_admin, m3_seed):
        await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp_b = await client_b_admin.get("/api/settlements")
        assert resp_b.status_code == 200
        assert len(resp_b.json()) == 0

    @pytest.mark.asyncio
    async def test_foreign_run_returns_404(self, client_a_admin, client_b_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp = await client_b_admin.get(f"/api/settlements/{run_id}")
        assert resp.status_code == 404


# ===========================================================================
# Test: RBAC
# ===========================================================================
class TestM3Rbac:
    @pytest.mark.asyncio
    async def test_readonly_cannot_create(self, client_a_readonly, m3_seed):
        resp = await client_a_readonly.post("/api/settlements", json={
            "property_id": m3_seed["property"],
            "lease_agreement_id": m3_seed["lease_a"],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_can_read(self, client_a_admin, client_a_readonly, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp = await client_a_readonly.get(f"/api/settlements/{run_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_cannot_finalize(self, client_a_admin, client_a_readonly, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
        resp = await client_a_readonly.post(f"/api/settlements/{run_id}/finalize")
        assert resp.status_code == 403


# ===========================================================================
# Test: immutability (finalized run + lines frozen, ADR-0003 trigger)
# ===========================================================================
class TestM3Immutability:
    @pytest.mark.asyncio
    async def test_update_total_cost_on_finalized_rejected(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, total_cost, balance, status, snapshot_sha256, engine_version, finalized_at) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','100.00','0.00','finalized',:sha,'m3.1.0',now())"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"], "sha": "a" * 64},
        )
        with pytest.raises(Exception, match="GoBD.*total_cost.*finalized"):
            await session.execute(
                text("UPDATE settlement_run SET total_cost='999.00' WHERE id=:id"), {"id": str(run_id)}
            )

    @pytest.mark.asyncio
    async def test_update_snapshot_on_finalized_rejected(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, status, snapshot_sha256, engine_version, finalized_at) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','finalized',:sha,'m3.1.0',now())"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"], "sha": "a" * 64},
        )
        with pytest.raises(Exception, match="GoBD.*snapshot_sha256.*finalized"):
            await session.execute(
                text("UPDATE settlement_run SET snapshot_sha256=:s WHERE id=:id"),
                {"id": str(run_id), "s": "b" * 64},
            )

    @pytest.mark.asyncio
    async def test_delete_finalized_run_rejected(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, status, engine_version, finalized_at) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','finalized','m3.1.0',now())"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"]},
        )
        with pytest.raises(Exception, match="GoBD.*DELETE.*finalized"):
            await session.execute(text("DELETE FROM settlement_run WHERE id=:id"), {"id": str(run_id)})

    @pytest.mark.asyncio
    async def test_superseded_by_id_allowed_on_finalized(self, app_rw_session, m3_seed):
        """The ONLY allowed mutation on a finalized run: setting superseded_by_id."""
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        a, b = uuid.uuid4(), uuid.uuid4()
        for rid, status in [(a, "finalized"), (b, "draft")]:
            await session.execute(
                text(
                    "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                    "advance_payments_source, status, engine_version) "
                    "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL',:st,'m3.1.0')"
                ),
                {"id": str(rid), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"], "st": status},
            )
        # Setting superseded_by_id on the finalized run is allowed.
        await session.execute(
            text("UPDATE settlement_run SET superseded_by_id=:b WHERE id=:a"),
            {"a": str(a), "b": str(b)},
        )
        result = await session.execute(
            text("SELECT superseded_by_id FROM settlement_run WHERE id=:a"), {"a": str(a)}
        )
        assert str(result.scalar_one()) == str(b)

    @pytest.mark.asyncio
    async def test_line_on_finalized_run_frozen(self, app_rw_session, m3_seed):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        run_id = uuid.uuid4()
        line_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO settlement_run (id, organization_id, property_id, run_type, period_start, period_end, "
                "advance_payments_source, status, engine_version) "
                "VALUES (:id,:org,:prop,'BK_MIETER','2025-01-01','2025-12-31','MANUAL','draft','m3.1.0')"
            ),
            {"id": str(run_id), "org": str(m3_seed["org_a_id"]), "prop": m3_seed["property"]},
        )
        await session.execute(
            text(
                "INSERT INTO settlement_line (id, organization_id, settlement_run_id, line_type, label, amount, sort_order) "
                "VALUES (:id,:org,:run,'COST','Test','100.00',0)"
            ),
            {"id": str(line_id), "org": str(m3_seed["org_a_id"]), "run": str(run_id)},
        )
        # While draft: line is editable.
        await session.execute(
            text("UPDATE settlement_line SET amount='200.00' WHERE id=:id"), {"id": str(line_id)}
        )
        # Finalize the run.
        await session.execute(
            text("UPDATE settlement_run SET status='finalized', finalized_at=now() WHERE id=:id"),
            {"id": str(run_id)},
        )
        # Now the line is frozen: UPDATE and DELETE both rejected.
        with pytest.raises(Exception, match="GoBD.*settlement_line.*finalized"):
            await session.execute(
                text("UPDATE settlement_line SET amount='300.00' WHERE id=:id"), {"id": str(line_id)}
            )


# ===========================================================================
# Test: compute is repeatable; preview does not persist
# ===========================================================================
class TestM3ComputeRepeatable:
    @pytest.mark.asyncio
    async def test_recompute_replaces_lines(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        r1 = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert r1.status_code == 200
        n1 = len(r1.json()["lines"])
        # Recompute: same input -> same line count, same total.
        r2 = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert r2.status_code == 200
        assert len(r2.json()["lines"]) == n1
        assert r1.json()["total_cost"] == r2.json()["total_cost"]

    @pytest.mark.asyncio
    async def test_missing_external_amounts_escalates(self, client_a_admin, m3_seed):
        """A HEIZUNG (EXTERNE_ABRECHNUNG) position without external amounts -> 422."""
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        # Provide consumption (for WASSER) but NO external amounts (for HEIZUNG).
        resp = await client_a_admin.post(
            f"/api/settlements/{run_id}/compute",
            json={"unit_metrics": _extras_for_exit(m3_seed)["unit_metrics"]},
        )
        assert resp.status_code == 422
        assert "escalate" in resp.json()["detail"].lower() or "extern" in resp.json()["detail"].lower()


# ===========================================================================
# End-to-end M3 EXIT: Mischobjekt with Mieterwechsel -> correct + complete PDF
# ===========================================================================
class TestM3ExitEndToEnd:
    @pytest.mark.asyncio
    async def test_full_bk_settlement_for_tenant_a(self, client_a_admin, m3_seed):
        """Create -> compute -> finalize -> PDF for tenant A (mid-year change).

        Hand-checked Soll (engine Golden 5, tenant A = U1, 181/365):
          GRUNDSTEUER 396.71, MUELL 59.51, WASSER 89.26, HEIZUNG 210.00 (extern),
          CO2 47.88, HAUSMEISTER 98.49; total 901.85; minus 700.00 = 201.85 Nachzahlung.
          WEG_VERWALTERKOSTEN (NICHT_UMLAGEFAEHIG) must NOT appear.
        """
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)

        # Finalize (computes, snapshots, hashes, writes lines -- atomic).
        fin = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        assert fin.status_code == 200, fin.text
        body = fin.json()
        assert body["status"] == "finalized"
        assert body["snapshot_sha256"] is not None and len(body["snapshot_sha256"]) == 64
        assert body["total_cost"] == "901.85"
        assert body["balance"] == "201.85"

        lines = {ln["cost_category_code"]: ln for ln in body["lines"] if ln["line_type"] == "COST"}
        assert "WEG_VERWALTERKOSTEN" not in lines  # not apportionable
        assert lines["GRUNDSTEUER"]["amount"] == "396.71"
        assert lines["GRUNDSTEUER"]["distribution_key"] == "WOHNFLAECHE"
        assert lines["GRUNDSTEUER"]["total_amount"] == "2900.00"
        assert lines["GRUNDSTEUER"]["time_share_days"] == 181
        assert lines["GRUNDSTEUER"]["time_share_total_days"] == 365
        assert lines["MUELLBESEITIGUNG"]["amount"] == "59.51"
        assert lines["KALTWASSER"]["amount"] == "89.26"
        assert lines["HEIZUNG_ZENTRAL"]["amount"] == "210.00"
        assert lines["HEIZUNG_ZENTRAL"]["note"] == "extern abgerechnet (Messdienst)"
        assert lines["CO2_KOSTEN"]["amount"] == "47.88"
        assert lines["CO2_KOSTEN"]["total_amount"] == "350.00"  # effective, not gross 500
        assert lines["HAUSMEISTER"]["amount"] == "98.49"

        # ADVANCE_PAYMENT and BALANCE lines present (BGH).
        types = {ln["line_type"] for ln in body["lines"]}
        assert "ADVANCE_PAYMENT" in types and "BALANCE" in types

        # PDF: only available finalized, contains the values (PDF computes nothing).
        pdf = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content[:5] == b"%PDF-"
        assert len(pdf.content) > 1000

    @pytest.mark.asyncio
    async def test_two_tenants_sum_to_unit_share(self, client_a_admin, m3_seed):
        """A + B for U1 GRUNDSTEUER == full unit share 800.00 (no cent lost)."""
        extras = _extras_for_exit(m3_seed)
        run_a = await _create_run(client_a_admin, m3_seed, "lease_a", "0.00")
        run_b = await _create_run(client_a_admin, m3_seed, "lease_b", "0.00")
        ra = (await client_a_admin.post(f"/api/settlements/{run_a}/finalize", json=extras)).json()
        rb = (await client_a_admin.post(f"/api/settlements/{run_b}/finalize", json=extras)).json()
        gs_a = Decimal(next(ln["amount"] for ln in ra["lines"] if ln["cost_category_code"] == "GRUNDSTEUER"))
        gs_b = Decimal(next(ln["amount"] for ln in rb["lines"] if ln["cost_category_code"] == "GRUNDSTEUER"))
        assert gs_a + gs_b == Decimal("800.00")

    @pytest.mark.asyncio
    async def test_pdf_only_for_finalized(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        # Draft (not finalized) -> PDF 409.
        resp = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_finalized_run_cannot_recompute(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        # Re-compute a finalized run -> 409 (service guards; correction = new run).
        resp = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json=extras)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_correction_creates_new_run(self, client_a_admin, m3_seed):
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        corr = await client_a_admin.post(f"/api/settlements/{run_id}/correction")
        assert corr.status_code == 201
        new_run = corr.json()
        assert new_run["status"] == "draft"
        assert new_run["id"] != run_id
        # Original now points to the new run (forward pointer), stays finalized.
        orig = await client_a_admin.get(f"/api/settlements/{run_id}")
        assert orig.json()["superseded_by_id"] == new_run["id"]
        assert orig.json()["status"] == "finalized"

    @pytest.mark.asyncio
    async def test_identical_results_retain_distinct_attestation_hashes(self, client_a_admin, m3_seed):
        """Same arithmetic, distinct evidence; persisted hashes remain stable."""
        extras = _extras_for_exit(m3_seed)
        r1 = await _create_run(client_a_admin, m3_seed, "lease_e", "1500.00")
        r2 = await _create_run(client_a_admin, m3_seed, "lease_e", "1500.00")
        f1 = (await client_a_admin.post(f"/api/settlements/{r1}/finalize", json=extras)).json()
        f2 = (await client_a_admin.post(f"/api/settlements/{r2}/finalize", json=extras)).json()
        for key in ("total_cost", "balance", "advance_payments_total"):
            assert f1[key] == f2[key]
        assert f1["snapshot_sha256"] != f2["snapshot_sha256"]
        assert (await client_a_admin.get(f"/api/settlements/{r1}")).json()["snapshot_sha256"] == f1["snapshot_sha256"]

    @pytest.mark.asyncio
    async def test_payload_consistent_with_lines(self, client_a_admin, m3_seed, app_rw_session):
        """snapshot_payload.result totals == settlement_line sums (I-G)."""
        run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "700.00")
        extras = _extras_for_exit(m3_seed)
        await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=extras)
        # Read the snapshot payload directly.
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m3_seed["org_a_id"])},
        )
        row = await app_rw_session.execute(
            text("SELECT snapshot_payload, total_cost, balance FROM settlement_run WHERE id=:id"),
            {"id": run_id},
        )
        payload, total_cost, balance = row.first()
        assert Decimal(payload["result"]["total_cost"]) == total_cost
        assert Decimal(payload["result"]["balance"]) == balance
        # Sum of COST lines in payload == total_cost.
        cost_sum = sum(
            Decimal(l["amount"]) for l in payload["result"]["lines"] if l["line_type"] == "COST"
        )
        assert cost_sum == total_cost


# ===========================================================================
# Regression: M1[)<->M3[] read-edge conversion (the live 500 reproduction)
# ===========================================================================
@pytest.fixture(scope="function")
def adjacent_lease_seed(seed_data):
    """One unit, two ADJACENT leases seeded in the TRUE M1 half-open [) convention.

    Weber valid_from=2025-01-01, valid_to=2025-07-01 (exclusive upper bound)
    Klein valid_from=2025-07-01, valid_to=NULL (open-ended)

    These do NOT overlap in the DB: the EXCLUDE uses daterange(.., '[)') and the
    point-in-time query uses valid_to > target, so 01.07. belongs only to Klein.
    Before the read-edge conversion the engine read both valid_to/valid_from as
    tag-inclusive, double-counted 01.07., and raised PeriodOverlapError -> 500.

    Function-scoped (not session) so it is independent of the big m3_seed and the
    DB is clean per test (the autouse run_migrations fixture rebuilds the schema).
    """
    import psycopg2

    org = str(seed_data["org_a_id"])
    ids = {
        "property": uuid.uuid4(),
        "u1": uuid.uuid4(),
        "person_owner": uuid.uuid4(),
        "owner_profile": uuid.uuid4(),
        "tp_weber": uuid.uuid4(),
        "tp_klein": uuid.uuid4(),
        "lease_weber": uuid.uuid4(),
        "lease_klein": uuid.uuid4(),
        "p_weber": uuid.uuid4(),
        "p_klein": uuid.uuid4(),
    }
    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        def cat(code):
            cur.execute(
                "SELECT id FROM cost_category WHERE code=%s AND plan_version=1", (code,)
            )
            return cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["property"]), org, "Adjacent-Lease Mischhaus", "MISCH"),
        )
        # Single unit, area 80 (the only unit -> it bears 100% of any Flaeche cost).
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["u1"]), org, str(ids["property"]), "1", "WOHNUNG", 80, 80, 300),
        )
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_owner"]), org, "Olga", "Owner"),
        )
        cur.execute(
            "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["owner_profile"]), org, str(ids["person_owner"])),
        )
        cur.execute(
            "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, mea_share) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), org, str(ids["u1"]), str(ids["owner_profile"]), date(2000, 1, 1), None),
        )
        for pkey, tpkey, first in [("p_weber", "tp_weber", "Walter"), ("p_klein", "tp_klein", "Karla")]:
            cur.execute(
                "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
                (str(ids[pkey]), org, first, "Mieter"),
            )
            cur.execute(
                "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
                (str(ids[tpkey]), org, str(ids[pkey])),
            )

        # The crux: adjacent valid_to = valid_from = 2025-07-01 in DB [) convention.
        leases = [
            (ids["lease_weber"], ids["tp_weber"], date(2025, 1, 1), date(2025, 7, 1)),
            (ids["lease_klein"], ids["tp_klein"], date(2025, 7, 1), None),
        ]
        for lid, tp, vf, vt in leases:
            cur.execute(
                "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, valid_from, valid_to, base_rent, advance_payment) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(lid), org, str(ids["u1"]), str(tp), vf, vt, Decimal("800.00"), Decimal("100.00")),
            )

        # A confirmed, full-year GRUNDSTEUER (WOHNFLAECHE). With a single unit the
        # unit bears the whole 1200.00, split purely by time between the two tenants.
        ce = uuid.uuid4()
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, cost_category_plan_version, "
            "invoice_date, service_period_from, service_period_to, gross_amount, tenant_distribution_key, "
            "tenant_apportionable, status, source, confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,'confirmed','MANUAL',now(),%s)",
            (
                str(ce), org, str(ids["property"]), str(cat("GRUNDSTEUER")),
                date(2025, 6, 1), date(2025, 1, 1), date(2025, 12, 31),
                Decimal("1200.00"), "WOHNFLAECHE", "UMLAGEFAEHIG",
                str(seed_data["user_a_admin_id"]),
            ),
        )
        ids["ce_grundsteuer"] = ce
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {**seed_data, **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in ids.items()}}


class TestM3AdjacentLeaseReadEdge:
    """The live bug: adjacent leases (M1 [)-convention) must compute, not 500.

    This is the regression test that was missing. It seeds via the DB in the real
    M1 convention and drives the public /compute endpoint, so it exercises the
    repository read-edge conversion (settlement_service.db_exclusive_to_engine_
    inclusive) end-to-end -- exactly where the conversion was missing.
    """

    async def _create_run(self, client, seed, lease_key):
        resp = await client.post("/api/settlements", json={
            "property_id": seed["property"],
            "lease_agreement_id": seed[lease_key],
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "advance_payments_total": "0.00",
        })
        assert resp.status_code == 201, resp.text
        await attest_fixture(client, "settlement", resp.json()["id"],
            {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
        return resp.json()["id"]

    @pytest.mark.asyncio
    async def test_adjacent_leases_compute_succeeds_no_500(self, client_a_admin, adjacent_lease_seed):
        """Compute for the predecessor tenant succeeds (no false overlap, no 500)."""
        run_id = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_weber")
        resp = await client_a_admin.post(f"/api/settlements/{run_id}/compute", json={})
        assert resp.status_code == 200, resp.text  # before the fix this was 500

    @pytest.mark.asyncio
    async def test_weber_181_days(self, client_a_admin, adjacent_lease_seed):
        """Predecessor Weber occupies 01.01.-30.06. = 181 days (DB 2025-07-01 -> 30.06.)."""
        run_id = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_weber")
        body = (await client_a_admin.post(f"/api/settlements/{run_id}/compute", json={})).json()
        gs = next(ln for ln in body["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        assert gs["time_share_days"] == 181
        assert gs["time_share_total_days"] == 365
        # 1200 * 181/365 = 595.0684... -> 595.07
        assert gs["amount"] == "595.07"

    @pytest.mark.asyncio
    async def test_klein_184_days(self, client_a_admin, adjacent_lease_seed):
        """Successor Klein occupies 01.07.-31.12. = 184 days (open-ended, clamped)."""
        run_id = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_klein")
        body = (await client_a_admin.post(f"/api/settlements/{run_id}/compute", json={})).json()
        gs = next(ln for ln in body["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        assert gs["time_share_days"] == 184
        assert gs["time_share_total_days"] == 365
        # 1200 * 184/365 = 604.9315... -> 604.93
        assert gs["amount"] == "604.93"

    @pytest.mark.asyncio
    async def test_time_shares_sum_to_one_no_vacancy(self, client_a_admin, adjacent_lease_seed):
        """Weber 181 + Klein 184 = 365 (Sigma time shares == 1); no vacancy day.

        The back-to-back boundary (30.06. -> 01.07.) leaves NO gap: the two tenant
        shares of the unit's GRUNDSTEUER add up to the full unit amount (1200.00),
        so no cent is attributed to vacancy/owner. This is the structural invariant
        the read-edge conversion preserves (ADR-0004 #4).
        """
        run_weber = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_weber")
        run_klein = await self._create_run(client_a_admin, adjacent_lease_seed, "lease_klein")
        bw = (await client_a_admin.post(f"/api/settlements/{run_weber}/compute", json={})).json()
        bk = (await client_a_admin.post(f"/api/settlements/{run_klein}/compute", json={})).json()
        gw = next(ln for ln in bw["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        gk = next(ln for ln in bk["lines"] if ln["cost_category_code"] == "GRUNDSTEUER")
        assert gw["time_share_days"] + gk["time_share_days"] == 365
        # No cent lost to a phantom 1-day vacancy: the two tenant shares == full cost.
        assert Decimal(gw["amount"]) + Decimal(gk["amount"]) == Decimal("1200.00")

    @pytest.mark.asyncio
    async def test_db_exclude_rejects_genuine_same_unit_overlap(
        self, client_a_admin, adjacent_lease_seed, app_rw_session
    ):
        """Primary guard: the DB EXCLUDE rejects a genuinely overlapping lease.

        A lease 2025-03-01..2025-09-01 on U1 overlaps Weber [2025-01-01,2025-07-01)
        even in the half-open DB convention, so excl_lease_no_overlap must reject the
        INSERT (fail-closed) -- a real overlap can never silently reach the engine.
        """
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(adjacent_lease_seed["org_a_id"])},
        )
        with pytest.raises(Exception):  # ExclusionViolation from excl_lease_no_overlap
            await app_rw_session.execute(
                text(
                    "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
                    "valid_from, valid_to, base_rent, advance_payment) "
                    "VALUES (:id,:org,:unit,:tp,'2025-03-01','2025-09-01','800.00','100.00')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org": str(adjacent_lease_seed["org_a_id"]),
                    "unit": adjacent_lease_seed["u1"],
                    "tp": adjacent_lease_seed["tp_weber"],
                },
            )


# ===========================================================================
# Unit test: the read-edge conversion helper itself
# ===========================================================================
class TestReadEdgeConversionHelper:
    def test_exclusive_to_inclusive_subtracts_one_day(self):
        from app.services.settlement_service import db_exclusive_to_engine_inclusive

        # DB stores the first NON-occupied day; engine wants the last occupied day.
        assert db_exclusive_to_engine_inclusive(date(2025, 7, 1)) == date(2025, 6, 30)
        assert db_exclusive_to_engine_inclusive(date(2025, 4, 1)) == date(2025, 3, 31)
        # Month/year boundary correctness.
        assert db_exclusive_to_engine_inclusive(date(2026, 1, 1)) == date(2025, 12, 31)

    def test_none_stays_open_ended(self):
        from app.services.settlement_service import db_exclusive_to_engine_inclusive

        assert db_exclusive_to_engine_inclusive(None) is None


@pytest.fixture
def derived_period_seed(seed_data):
    import psycopg2
    seed = _seed_m3(seed_data)
    bank = str(uuid.uuid4())
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO bank_account (id,organization_id,property_id,account_type,label) VALUES (%s,%s,%s,'LAUFEND','T08')",
                        (bank, str(seed["org_a_id"]), seed["property"]))
            for year in (2025, 2026):
                for month in range(1, 13):
                    for kind, amount in (("NK_VORAUSZAHLUNG", "100.00"), ("KALTMIETE", "500.00")):
                        cur.execute("""INSERT INTO payment
                            (id,organization_id,bank_account_id,property_id,direction,amount,value_date,
                             payment_type,tenant_profile_id,lease_agreement_id,period_year,period_month,
                             status,booked_at,booked_by)
                            VALUES (%s,%s,%s,%s,'IN',%s,%s,%s,%s,%s,%s,%s,'booked',now(),%s)""",
                            (str(uuid.uuid4()), str(seed["org_a_id"]), bank, seed["property"], amount,
                             date(year, month, 1), kind, seed["tp_e"], seed["lease_e"], year, month,
                             str(seed["user_a_admin_id"])))
            cur.execute("""INSERT INTO payment
                (id,organization_id,bank_account_id,property_id,direction,amount,value_date,
                 payment_type,tenant_profile_id,lease_agreement_id,period_year,period_month,status)
                VALUES (%s,%s,%s,%s,'IN',999,'2025-08-01','NK_VORAUSZAHLUNG',%s,%s,2025,8,'draft')""",
                (str(uuid.uuid4()), str(seed["org_a_id"]), bank, seed["property"], seed["tp_e"], seed["lease_e"]))
    return seed


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end,expected", [
    ("2025-07-01", "2026-06-30", "1200.00"),
    ("2025-01-01", "2025-09-30", "900.00"),
])
async def test_derived_advances_for_exact_period(client_a_admin, derived_period_seed, start, end, expected):
    import psycopg2
    seed = derived_period_seed
    if end == "2025-09-30":
        with psycopg2.connect(SYNC_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE lease_agreement SET valid_to='2025-10-01' WHERE id=%s", (seed["lease_e"],))
    resp = await client_a_admin.post("/api/settlements", json={
        "property_id": seed["property"], "lease_agreement_id": seed["lease_e"],
        "period_start": start, "period_end": end, "advance_payments_total": "0.00",
    })
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]
    resp = await client_a_admin.patch(f"/api/settlements/{run_id}/advance-payments-derived", params={"period_year": 2025})
    assert resp.status_code == 200, resp.text
    assert resp.json()["advance_payments_total"] == expected
    assert resp.json()["advance_payments_source"] == "DERIVED"
