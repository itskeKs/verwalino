"""M6 bank accounts API tests -- list, get, create, RBAC, RLS."""

from __future__ import annotations

import os
import uuid

import pytest
import psycopg2

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.fixture(scope="session")
def ba_seed(seed_data):
    org_a = str(seed_data["org_a_id"])
    org_b = str(seed_data["org_b_id"])
    ids = {
        "prop_a": uuid.uuid4(),
        "prop_b_org": uuid.uuid4(),
        "acct_a_1": uuid.uuid4(),
        "acct_a_2": uuid.uuid4(),
        "acct_b": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_a"]), org_a, "BA Haus A", "MIET"),
        )
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_b_org"]), org_b, "BA Haus B", "WEG"),
        )
        # Pre-existing accounts
        for aid, pid, org, label, atype in [
            (ids["acct_a_1"], ids["prop_a"], org_a, "Girokonto", "LAUFEND"),
            (ids["acct_a_2"], ids["prop_a"], org_a, "Ruecklage", "RUECKLAGE"),
            (ids["acct_b"], ids["prop_b_org"], org_b, "WEG Konto", "LAUFEND"),
        ]:
            cur.execute(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(aid), str(org), str(pid), atype, label),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**ids, "org_a": seed_data["org_a_id"], "org_b": seed_data["org_b_id"]}


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_all(client_a_admin, ba_seed):
    r = await client_a_admin.get("/api/bank-accounts")
    assert r.status_code == 200
    data = r.json()
    ids = {d["id"] for d in data}
    assert str(ba_seed["acct_a_1"]) in ids
    assert str(ba_seed["acct_a_2"]) in ids
    # org_b account must NOT appear
    assert str(ba_seed["acct_b"]) not in ids


@pytest.mark.asyncio
async def test_list_by_property(client_a_admin, ba_seed):
    r = await client_a_admin.get(f"/api/bank-accounts?property_id={ba_seed['prop_a']}")
    assert r.status_code == 200
    data = r.json()
    # At least the two we seeded
    labels = {d["label"] for d in data}
    assert "Girokonto" in labels
    assert "Ruecklage" in labels


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_own(client_a_admin, ba_seed):
    r = await client_a_admin.get(f"/api/bank-accounts/{ba_seed['acct_a_1']}")
    assert r.status_code == 200
    assert r.json()["label"] == "Girokonto"


@pytest.mark.asyncio
async def test_get_foreign_404(client_a_admin, ba_seed):
    """Cross-org access -> 404 (not 403, RLS)."""
    r = await client_a_admin.get(f"/api/bank-accounts/{ba_seed['acct_b']}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_success(client_a_admin, ba_seed):
    r = await client_a_admin.post(
        "/api/bank-accounts",
        json={
            "property_id": str(ba_seed["prop_a"]),
            "account_type": "KAUTION",
            "label": "Kautionskonto",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["account_type"] == "KAUTION"
    assert data["label"] == "Kautionskonto"
    assert data["property_id"] == str(ba_seed["prop_a"])


@pytest.mark.asyncio
async def test_create_invalid_type(client_a_admin, ba_seed):
    r = await client_a_admin.post(
        "/api/bank-accounts",
        json={
            "property_id": str(ba_seed["prop_a"]),
            "account_type": "INVALID",
            "label": "Nope",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_readonly_forbidden(client_a_readonly, ba_seed):
    r = await client_a_readonly.post(
        "/api/bank-accounts",
        json={
            "property_id": str(ba_seed["prop_a"]),
            "account_type": "LAUFEND",
            "label": "Blocked",
        },
    )
    assert r.status_code == 403
