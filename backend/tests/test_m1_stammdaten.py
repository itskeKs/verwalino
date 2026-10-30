"""M1 Stammdaten tests -- the M1 exit criterion.

Exit criterion (roadmap): "Ein Mischobjekt mit Eigentuemer- und Mieterwechsel
innerhalb eines Jahres laesst sich lueckenlos und ueberschneidungsfrei abbilden."

Concrete tests:
  1. EXCLUDE rejects overlapping ownership periods (same owner/unit); accepts co-owners.
  2. EXCLUDE rejects overlapping lease periods per unit; accepts gaps (vacancy).
  3. Point-in-time query: "who was owner/tenant of unit X on date Y?" returns correctly.
  4. Tenant isolation for all new M1 tables (RLS).
  5. Full scenario: Mischobjekt with owner change + tenant change within one year.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ============================================================================
# Helper: seed a full Mischobjekt scenario for org_a
# ============================================================================
async def _seed_m1_scenario(session, org_id: str):
    """Seed a Mischobjekt with units, persons, profiles, ownership and lease history.

    Returns a dict of all created IDs for assertions.
    """
    prop_id = str(uuid.uuid4())
    unit_1_id = str(uuid.uuid4())
    unit_2_id = str(uuid.uuid4())

    # Persons
    person_owner1_id = str(uuid.uuid4())
    person_owner2_id = str(uuid.uuid4())
    person_tenant1_id = str(uuid.uuid4())
    person_tenant2_id = str(uuid.uuid4())

    # Profiles
    op1_id = str(uuid.uuid4())
    op2_id = str(uuid.uuid4())
    tp1_id = str(uuid.uuid4())
    tp2_id = str(uuid.uuid4())

    # Ownership history
    oh1_id = str(uuid.uuid4())
    oh2_id = str(uuid.uuid4())

    # Lease agreements
    la1_id = str(uuid.uuid4())
    la2_id = str(uuid.uuid4())

    # Property: Mischobjekt
    await session.execute(text(
        "INSERT INTO property (id, organization_id, name, management_type) "
        "VALUES (:id, :org, 'Hauptstr. 5', 'MISCH')"
    ), {"id": prop_id, "org": org_id})

    # Units
    await session.execute(text(
        "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
        "VALUES (:id, :org, :pid, 'EG-01', 'WOHNUNG', 85.50, 80.00, 350.000000)"
    ), {"id": unit_1_id, "org": org_id, "pid": prop_id})

    await session.execute(text(
        "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, mea) "
        "VALUES (:id, :org, :pid, 'OG-01', 'GEWERBE', 120.00, 650.000000)"
    ), {"id": unit_2_id, "org": org_id, "pid": prop_id})

    # Persons
    for pid, first, last in [
        (person_owner1_id, "Hans", "Mueller"),
        (person_owner2_id, "Petra", "Schmidt"),
        (person_tenant1_id, "Max", "Meier"),
        (person_tenant2_id, "Lisa", "Weber"),
    ]:
        await session.execute(text(
            "INSERT INTO person (id, organization_id, first_name, last_name) "
            "VALUES (:id, :org, :first, :last)"
        ), {"id": pid, "org": org_id, "first": first, "last": last})

    # Owner profiles
    await session.execute(text(
        "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (:id, :org, :pid)"
    ), {"id": op1_id, "org": org_id, "pid": person_owner1_id})
    await session.execute(text(
        "INSERT INTO owner_profile (id, organization_id, person_id) VALUES (:id, :org, :pid)"
    ), {"id": op2_id, "org": org_id, "pid": person_owner2_id})

    # Tenant profiles
    await session.execute(text(
        "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (:id, :org, :pid)"
    ), {"id": tp1_id, "org": org_id, "pid": person_tenant1_id})
    await session.execute(text(
        "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (:id, :org, :pid)"
    ), {"id": tp2_id, "org": org_id, "pid": person_tenant2_id})

    # Ownership history: owner1 owns unit_1 from 2025-01-01 to 2025-07-01,
    # then owner2 owns it from 2025-07-01 onwards (open-ended)
    await session.execute(text(
        "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, valid_to, mea_share) "
        "VALUES (:id, :org, :uid, :opid, '2025-01-01', '2025-07-01', 350.000000)"
    ), {"id": oh1_id, "org": org_id, "uid": unit_1_id, "opid": op1_id})
    await session.execute(text(
        "INSERT INTO ownership_history (id, organization_id, unit_id, owner_profile_id, valid_from, valid_to, mea_share) "
        "VALUES (:id, :org, :uid, :opid, '2025-07-01', NULL, 350.000000)"
    ), {"id": oh2_id, "org": org_id, "uid": unit_1_id, "opid": op2_id})

    # Lease agreements: tenant1 rents unit_1 from 2025-01-01 to 2025-06-01,
    # gap (vacancy) June, tenant2 from 2025-07-01 onwards
    await session.execute(text(
        "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
        "valid_from, valid_to, base_rent, advance_payment) "
        "VALUES (:id, :org, :uid, :tpid, '2025-01-01', '2025-06-01', 750.00, 200.00)"
    ), {"id": la1_id, "org": org_id, "uid": unit_1_id, "tpid": tp1_id})
    await session.execute(text(
        "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
        "valid_from, valid_to, base_rent, advance_payment, deposit) "
        "VALUES (:id, :org, :uid, :tpid, '2025-07-01', NULL, 800.00, 220.00, 2400.00)"
    ), {"id": la2_id, "org": org_id, "uid": unit_1_id, "tpid": tp2_id})

    return {
        "prop_id": prop_id,
        "unit_1_id": unit_1_id,
        "unit_2_id": unit_2_id,
        "person_owner1_id": person_owner1_id,
        "person_owner2_id": person_owner2_id,
        "person_tenant1_id": person_tenant1_id,
        "person_tenant2_id": person_tenant2_id,
        "op1_id": op1_id,
        "op2_id": op2_id,
        "tp1_id": tp1_id,
        "tp2_id": tp2_id,
        "oh1_id": oh1_id,
        "oh2_id": oh2_id,
        "la1_id": la1_id,
        "la2_id": la2_id,
    }


# ============================================================================
# 1. EXCLUDE rejects overlapping ownership for SAME (unit, owner_profile)
# ============================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("route, table, field, key", [
    ("leases", "lease_agreement", "unit_id", "unit_1_id"),
    ("leases", "lease_agreement", "tenant_profile_id", "tp1_id"),
    ("ownership", "ownership_history", "unit_id", "unit_1_id"),
    ("ownership", "ownership_history", "owner_profile_id", "op1_id"),
    ("units", "unit", "property_id", "prop_id"),
])
async def test_foreign_and_unknown_parents_are_not_found_before_writing(
    client_a_admin, migrator_session, seed_data, route, table, field, key,
):
    async with AsyncSession(migrator_session.bind) as setup:
        own = await _seed_m1_scenario(setup, str(seed_data["org_a_id"]))
        foreign = await _seed_m1_scenario(setup, str(seed_data["org_b_id"]))
        await setup.commit()
    bodies = {
        "leases": {"unit_id": own["unit_1_id"], "tenant_profile_id": own["tp1_id"],
                   "valid_from": "2025-08-01", "base_rent": "500", "advance_payment": "100"},
        "ownership": {"unit_id": own["unit_1_id"], "owner_profile_id": own["op1_id"],
                      "valid_from": "2025-03-01"},
        "units": {"property_id": own["prop_id"], "number": "Rejected parent", "usage_type": "WOHNUNG"},
    }
    before = await migrator_session.scalar(text(f"SELECT count(*) FROM {table}"))
    responses = []
    for parent in (foreign[key], str(uuid.uuid4())):
        response = await client_a_admin.post(f"/api/{route}", json=bodies[route] | {field: parent})
        assert response.status_code == 404, response.text
        responses.append(response.json())
    assert responses[0] == responses[1]
    assert await migrator_session.scalar(text(f"SELECT count(*) FROM {table}")) == before


class TestOwnershipExclude:
    @pytest.mark.asyncio
    async def test_overlap_same_owner_rejected(self, migrator_session, seed_data):
        """Insert two overlapping ownership records for the SAME owner -> rejected."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        # Try to insert an overlapping record: op1 owns unit_1 2025-03-01..2025-09-01
        # This overlaps with the existing 2025-01-01..2025-07-01 for the same (unit, owner)
        with pytest.raises(Exception) as exc_info:
            await migrator_session.execute(text(
                "INSERT INTO ownership_history "
                "(id, organization_id, unit_id, owner_profile_id, valid_from, valid_to, mea_share) "
                "VALUES (:id, :org, :uid, :opid, '2025-03-01', '2025-09-01', 350.000000)"
            ), {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "uid": ids["unit_1_id"],
                "opid": ids["op1_id"],  # same owner
            })
        assert "excl_ownership_no_overlap" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_coowners_same_period_allowed(self, migrator_session, seed_data):
        """Two DIFFERENT owners for the same unit at the same time -> allowed (co-ownership)."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        # op1 already owns unit_2 implicitly not -- let's add two co-owners for unit_2
        await migrator_session.execute(text(
            "INSERT INTO ownership_history "
            "(id, organization_id, unit_id, owner_profile_id, valid_from, valid_to, mea_share) "
            "VALUES (:id, :org, :uid, :opid, '2025-01-01', NULL, 325.000000)"
        ), {
            "id": str(uuid.uuid4()),
            "org": org_id,
            "uid": ids["unit_2_id"],
            "opid": ids["op1_id"],
        })

        # Second co-owner for the same unit, same period -> should succeed
        await migrator_session.execute(text(
            "INSERT INTO ownership_history "
            "(id, organization_id, unit_id, owner_profile_id, valid_from, valid_to, mea_share) "
            "VALUES (:id, :org, :uid, :opid, '2025-01-01', NULL, 325.000000)"
        ), {
            "id": str(uuid.uuid4()),
            "org": org_id,
            "uid": ids["unit_2_id"],
            "opid": ids["op2_id"],  # different owner
        })
        # If we reach here, the co-ownership was accepted


# ============================================================================
# 2. EXCLUDE rejects overlapping leases per unit; gaps allowed
# ============================================================================
class TestLeaseExclude:
    @pytest.mark.asyncio
    async def test_overlap_lease_rejected(self, migrator_session, seed_data):
        """Insert two overlapping leases for the same unit -> rejected."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        # Try to insert a lease overlapping with la1 (2025-01-01..2025-06-01)
        with pytest.raises(Exception) as exc_info:
            await migrator_session.execute(text(
                "INSERT INTO lease_agreement "
                "(id, organization_id, unit_id, tenant_profile_id, "
                "valid_from, valid_to, base_rent, advance_payment) "
                "VALUES (:id, :org, :uid, :tpid, '2025-03-01', '2025-08-01', 700.00, 180.00)"
            ), {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "uid": ids["unit_1_id"],
                "tpid": ids["tp2_id"],  # different tenant, same unit
            })
        assert "excl_lease_no_overlap" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_gap_vacancy_allowed(self, migrator_session, seed_data):
        """Gap between leases (vacancy) is allowed."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)
        # The scenario already has a gap: la1 ends 2025-06-01, la2 starts 2025-07-01
        # June is vacant. This was seeded without error -> gaps are allowed.

        # Verify both leases exist
        result = await migrator_session.execute(text(
            "SELECT id FROM lease_agreement WHERE unit_id = :uid ORDER BY valid_from"
        ), {"uid": ids["unit_1_id"]})
        leases = result.fetchall()
        assert len(leases) == 2

    @pytest.mark.asyncio
    async def test_different_units_same_period_allowed(self, migrator_session, seed_data):
        """Different units can have leases at the same time."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        # Lease on unit_2 in the same period as unit_1 -> allowed
        await migrator_session.execute(text(
            "INSERT INTO lease_agreement "
            "(id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) "
            "VALUES (:id, :org, :uid, :tpid, '2025-01-01', '2025-12-31', 1200.00, 300.00)"
        ), {
            "id": str(uuid.uuid4()),
            "org": org_id,
            "uid": ids["unit_2_id"],  # different unit
            "tpid": ids["tp1_id"],
        })


# ============================================================================
# 3. Point-in-time queries
# ============================================================================
class TestPointInTimeQueries:
    @pytest.mark.asyncio
    async def test_owner_at_date_before_change(self, migrator_session, seed_data):
        """On 2025-03-15, owner1 owns unit_1."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        result = await migrator_session.execute(text(
            "SELECT owner_profile_id, mea_share FROM ownership_history "
            "WHERE unit_id = :uid "
            "  AND valid_from <= '2025-03-15' "
            "  AND (valid_to IS NULL OR valid_to > '2025-03-15')"
        ), {"uid": ids["unit_1_id"]})
        rows = result.fetchall()
        assert len(rows) == 1
        assert str(rows[0].owner_profile_id) == ids["op1_id"]
        assert rows[0].mea_share == Decimal("350.000000")

    @pytest.mark.asyncio
    async def test_owner_at_date_after_change(self, migrator_session, seed_data):
        """On 2025-09-01, owner2 owns unit_1 (open-ended)."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        result = await migrator_session.execute(text(
            "SELECT owner_profile_id FROM ownership_history "
            "WHERE unit_id = :uid "
            "  AND valid_from <= '2025-09-01' "
            "  AND (valid_to IS NULL OR valid_to > '2025-09-01')"
        ), {"uid": ids["unit_1_id"]})
        rows = result.fetchall()
        assert len(rows) == 1
        assert str(rows[0].owner_profile_id) == ids["op2_id"]

    @pytest.mark.asyncio
    async def test_tenant_at_date_during_lease1(self, migrator_session, seed_data):
        """On 2025-02-15, tenant1 rents unit_1."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        result = await migrator_session.execute(text(
            "SELECT tenant_profile_id, base_rent FROM lease_agreement "
            "WHERE unit_id = :uid "
            "  AND valid_from <= '2025-02-15' "
            "  AND (valid_to IS NULL OR valid_to > '2025-02-15')"
        ), {"uid": ids["unit_1_id"]})
        rows = result.fetchall()
        assert len(rows) == 1
        assert str(rows[0].tenant_profile_id) == ids["tp1_id"]
        assert rows[0].base_rent == Decimal("750.00")

    @pytest.mark.asyncio
    async def test_vacancy_during_gap(self, migrator_session, seed_data):
        """On 2025-06-15, no tenant for unit_1 (vacancy gap)."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        result = await migrator_session.execute(text(
            "SELECT tenant_profile_id FROM lease_agreement "
            "WHERE unit_id = :uid "
            "  AND valid_from <= '2025-06-15' "
            "  AND (valid_to IS NULL OR valid_to > '2025-06-15')"
        ), {"uid": ids["unit_1_id"]})
        rows = result.fetchall()
        assert len(rows) == 0  # Vacancy

    @pytest.mark.asyncio
    async def test_tenant_at_date_during_lease2(self, migrator_session, seed_data):
        """On 2025-10-01, tenant2 rents unit_1."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        result = await migrator_session.execute(text(
            "SELECT tenant_profile_id, base_rent, deposit FROM lease_agreement "
            "WHERE unit_id = :uid "
            "  AND valid_from <= '2025-10-01' "
            "  AND (valid_to IS NULL OR valid_to > '2025-10-01')"
        ), {"uid": ids["unit_1_id"]})
        rows = result.fetchall()
        assert len(rows) == 1
        assert str(rows[0].tenant_profile_id) == ids["tp2_id"]
        assert rows[0].base_rent == Decimal("800.00")
        assert rows[0].deposit == Decimal("2400.00")


# ============================================================================
# 4. RLS isolation for M1 tables
# ============================================================================
class TestM1TenantIsolation:
    """Verify that M1 tables respect tenant isolation (RLS)."""

    @pytest.mark.asyncio
    async def test_property_isolation(self, app_rw_session, seed_data):
        """Properties created for org_a are invisible to org_b context."""
        session = app_rw_session

        # Create a property as org_a
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        prop_id = str(uuid.uuid4())
        await session.execute(text(
            "INSERT INTO property (id, organization_id, name, management_type) "
            "VALUES (:id, :org, 'Test Property A', 'MIET')"
        ), {"id": prop_id, "org": str(seed_data["org_a_id"])})

        # Switch to org_b
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_b_id"])},
        )

        # org_b should see zero properties
        result = await session.execute(text("SELECT id FROM property"))
        rows = result.fetchall()
        org_b_prop_ids = {str(r.id) for r in rows}
        assert prop_id not in org_b_prop_ids

    @pytest.mark.asyncio
    async def test_person_isolation(self, app_rw_session, seed_data):
        """Persons created for org_a are invisible to org_b context."""
        session = app_rw_session

        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )
        person_id = str(uuid.uuid4())
        await session.execute(text(
            "INSERT INTO person (id, organization_id, last_name) "
            "VALUES (:id, :org, 'Testperson')"
        ), {"id": person_id, "org": str(seed_data["org_a_id"])})

        # Switch to org_b
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_b_id"])},
        )

        result = await session.execute(text("SELECT id FROM person"))
        rows = result.fetchall()
        person_ids = {str(r.id) for r in rows}
        assert person_id not in person_ids

    @pytest.mark.asyncio
    async def test_write_isolation_property(self, app_rw_session, seed_data):
        """Cannot INSERT a property with a foreign org_id (WITH CHECK)."""
        session = app_rw_session

        # Set context to org_a
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(seed_data["org_a_id"])},
        )

        # Try to insert a property belonging to org_b
        with pytest.raises(Exception):
            await session.execute(text(
                "INSERT INTO property (id, organization_id, name, management_type) "
                "VALUES (:id, :org, 'Sneaky', 'MIET')"
            ), {"id": str(uuid.uuid4()), "org": str(seed_data["org_b_id"])})

    @pytest.mark.asyncio
    async def test_fail_closed_property(self, app_rw_session):
        """No context set -> zero properties."""
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )
        result = await session.execute(text("SELECT * FROM property"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_fail_closed_ownership_history(self, app_rw_session):
        """No context set -> zero ownership_history rows."""
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )
        result = await session.execute(text("SELECT * FROM ownership_history"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_fail_closed_lease_agreement(self, app_rw_session):
        """No context set -> zero lease_agreement rows."""
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )
        result = await session.execute(text("SELECT * FROM lease_agreement"))
        assert len(result.fetchall()) == 0


# ============================================================================
# 5. Full scenario: Mischobjekt with owner + tenant change within one year
# ============================================================================
class TestMischobjektScenario:
    """The complete M1 exit criterion test: a Mischobjekt with ownership and
    tenant changes within one year, seamlessly and without overlap."""

    @pytest.mark.asyncio
    async def test_full_scenario(self, migrator_session, seed_data):
        """Seed the complete scenario and verify all invariants hold."""
        org_id = str(seed_data["org_a_id"])
        ids = await _seed_m1_scenario(migrator_session, org_id)

        # Verify property is MISCH
        result = await migrator_session.execute(text(
            "SELECT management_type FROM property WHERE id = :id"
        ), {"id": ids["prop_id"]})
        assert result.scalar() == "MISCH"

        # Verify units exist
        result = await migrator_session.execute(text(
            "SELECT number, usage_type FROM unit WHERE property_id = :pid ORDER BY number"
        ), {"pid": ids["prop_id"]})
        units = result.fetchall()
        assert len(units) == 2
        assert units[0].usage_type == "WOHNUNG"
        assert units[1].usage_type == "GEWERBE"

        # Verify ownership timeline is seamless (no gap between owner1 and owner2)
        result = await migrator_session.execute(text(
            "SELECT valid_from, valid_to, owner_profile_id "
            "FROM ownership_history WHERE unit_id = :uid ORDER BY valid_from"
        ), {"uid": ids["unit_1_id"]})
        oh_rows = result.fetchall()
        assert len(oh_rows) == 2
        # owner1: [2025-01-01, 2025-07-01)
        assert oh_rows[0].valid_from == date(2025, 1, 1)
        assert oh_rows[0].valid_to == date(2025, 7, 1)
        # owner2: [2025-07-01, NULL) -- seamless transition
        assert oh_rows[1].valid_from == date(2025, 7, 1)
        assert oh_rows[1].valid_to is None

        # Verify lease timeline with one month vacancy
        result = await migrator_session.execute(text(
            "SELECT valid_from, valid_to, tenant_profile_id, base_rent "
            "FROM lease_agreement WHERE unit_id = :uid ORDER BY valid_from"
        ), {"uid": ids["unit_1_id"]})
        la_rows = result.fetchall()
        assert len(la_rows) == 2
        # tenant1: [2025-01-01, 2025-06-01)
        assert la_rows[0].valid_from == date(2025, 1, 1)
        assert la_rows[0].valid_to == date(2025, 6, 1)
        assert la_rows[0].base_rent == Decimal("750.00")
        # tenant2: [2025-07-01, NULL)
        assert la_rows[1].valid_from == date(2025, 7, 1)
        assert la_rows[1].valid_to is None
        assert la_rows[1].base_rent == Decimal("800.00")

        # Point-in-time checks across the year
        # January: owner1 + tenant1
        r = await migrator_session.execute(text(
            "SELECT owner_profile_id FROM ownership_history "
            "WHERE unit_id = :uid AND valid_from <= '2025-01-15' "
            "AND (valid_to IS NULL OR valid_to > '2025-01-15')"
        ), {"uid": ids["unit_1_id"]})
        assert str(r.scalar()) == ids["op1_id"]

        r = await migrator_session.execute(text(
            "SELECT tenant_profile_id FROM lease_agreement "
            "WHERE unit_id = :uid AND valid_from <= '2025-01-15' "
            "AND (valid_to IS NULL OR valid_to > '2025-01-15')"
        ), {"uid": ids["unit_1_id"]})
        assert str(r.scalar()) == ids["tp1_id"]

        # June 15: owner1 still owns, but unit is vacant (no tenant)
        r = await migrator_session.execute(text(
            "SELECT owner_profile_id FROM ownership_history "
            "WHERE unit_id = :uid AND valid_from <= '2025-06-15' "
            "AND (valid_to IS NULL OR valid_to > '2025-06-15')"
        ), {"uid": ids["unit_1_id"]})
        assert str(r.scalar()) == ids["op1_id"]

        r = await migrator_session.execute(text(
            "SELECT count(*) FROM lease_agreement "
            "WHERE unit_id = :uid AND valid_from <= '2025-06-15' "
            "AND (valid_to IS NULL OR valid_to > '2025-06-15')"
        ), {"uid": ids["unit_1_id"]})
        assert r.scalar() == 0  # vacancy

        # September: owner2 + tenant2
        r = await migrator_session.execute(text(
            "SELECT owner_profile_id FROM ownership_history "
            "WHERE unit_id = :uid AND valid_from <= '2025-09-01' "
            "AND (valid_to IS NULL OR valid_to > '2025-09-01')"
        ), {"uid": ids["unit_1_id"]})
        assert str(r.scalar()) == ids["op2_id"]

        r = await migrator_session.execute(text(
            "SELECT tenant_profile_id FROM lease_agreement "
            "WHERE unit_id = :uid AND valid_from <= '2025-09-01' "
            "AND (valid_to IS NULL OR valid_to > '2025-09-01')"
        ), {"uid": ids["unit_1_id"]})
        assert str(r.scalar()) == ids["tp2_id"]
