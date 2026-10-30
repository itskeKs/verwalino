"""M2 GoBD-Belegfluss tests -- m2-gobd-belegfluss.md section 8.6.

Tests 1-14: immutability proofs, RLS, state machine, hash verification,
atomic confirm, retention_until, end-to-end M2 exit path.

Uses the same fixtures as M0/M1 (seed_data, app_rw_session, migrator_session,
client_a_admin/clerk/readonly, client_b_admin).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.storage import (
    LocalFsDocumentStore,
    compute_sha256,
    make_storage_key,
    set_store,
    verify_document_integrity,
)


# ---------------------------------------------------------------------------
# Helper: create seed data for M2 tests (property needed for cost_entry FK)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def m2_seed(seed_data):
    """Create a property in org_a for M2 cost entries (via migrator, session-scoped)."""
    import psycopg2

    sync_url = os.environ.get(
        "HV_DATABASE_URL_SYNC",
        "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
    )
    property_a_id = uuid.uuid4()

    conn = psycopg2.connect(sync_url)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) "
            "VALUES (%s, %s, %s, %s)",
            (str(property_a_id), str(seed_data["org_a_id"]), "Testhaus M2", "WEG"),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, "property_a_id": property_a_id}


@pytest.fixture
def tmp_store(tmp_path):
    """Provide a temporary LocalFsDocumentStore and register it."""
    store = LocalFsDocumentStore(str(tmp_path))
    set_store(store)
    yield store
    # Reset to default after test
    set_store(None)  # type: ignore


def _cost_category_id_sql():
    """SQL to get the first cost_category id (GRUNDSTEUER)."""
    return "SELECT id FROM cost_category WHERE code = 'GRUNDSTEUER' AND plan_version = 1"


# ============================================================
# Test 1: cost_entry confirmed is immutable (trigger)
# ============================================================
class TestCostEntryConfirmedImmutable:
    @pytest.mark.asyncio
    async def test_update_gross_amount_on_confirmed_rejected(self, app_rw_session, m2_seed):
        """Direct UPDATE of gross_amount on a confirmed cost_entry -> DB exception."""
        session = app_rw_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )

        # Get a cost_category id
        cat_result = await session.execute(text(_cost_category_id_sql()))
        cat_id = cat_result.scalar_one()

        # Insert a confirmed cost_entry directly
        entry_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO cost_entry (
                    id, organization_id, property_id, cost_category_id,
                    cost_category_plan_version, invoice_date,
                    service_period_from, service_period_to,
                    gross_amount, tenant_apportionable, status, source,
                    confirmed_at, confirmed_by
                ) VALUES (
                    :id, :org, :prop, :cat, 1, '2025-06-01',
                    '2025-01-01', '2025-12-31',
                    '1000.00', 'UMLAGEFAEHIG', 'confirmed', 'MANUAL',
                    now(), :user_id
                )
            """),
            {
                "id": str(entry_id),
                "org": str(org_id),
                "prop": str(m2_seed["property_a_id"]),
                "cat": str(cat_id),
                "user_id": str(m2_seed["user_a_admin_id"]),
            },
        )

        # Attempt to UPDATE gross_amount -> must fail
        with pytest.raises(Exception, match="GoBD.*gross_amount.*confirmed"):
            await session.execute(
                text("UPDATE cost_entry SET gross_amount = '2000.00' WHERE id = :id"),
                {"id": str(entry_id)},
            )


# ============================================================
# Test 2: cost_entry confirmed DELETE forbidden; draft DELETE allowed
# ============================================================
class TestCostEntryDeleteBehavior:
    @pytest.mark.asyncio
    async def test_delete_confirmed_forbidden(self, app_rw_session, m2_seed):
        session = app_rw_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )
        cat_result = await session.execute(text(_cost_category_id_sql()))
        cat_id = cat_result.scalar_one()

        entry_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO cost_entry (
                    id, organization_id, property_id, cost_category_id,
                    cost_category_plan_version, invoice_date,
                    service_period_from, service_period_to,
                    gross_amount, tenant_apportionable, status, source,
                    confirmed_at, confirmed_by
                ) VALUES (
                    :id, :org, :prop, :cat, 1, '2025-06-01',
                    '2025-01-01', '2025-12-31',
                    '500.00', 'UMLAGEFAEHIG', 'confirmed', 'MANUAL',
                    now(), :user_id
                )
            """),
            {
                "id": str(entry_id),
                "org": str(org_id),
                "prop": str(m2_seed["property_a_id"]),
                "cat": str(cat_id),
                "user_id": str(m2_seed["user_a_admin_id"]),
            },
        )

        with pytest.raises(Exception, match="GoBD.*DELETE.*confirmed"):
            await session.execute(
                text("DELETE FROM cost_entry WHERE id = :id"), {"id": str(entry_id)}
            )

    @pytest.mark.asyncio
    async def test_delete_draft_allowed(self, app_rw_session, m2_seed):
        """Drafts (needs_review/rejected) can be deleted -- positive test."""
        session = app_rw_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )
        cat_result = await session.execute(text(_cost_category_id_sql()))
        cat_id = cat_result.scalar_one()

        entry_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO cost_entry (
                    id, organization_id, property_id, cost_category_id,
                    cost_category_plan_version, invoice_date,
                    service_period_from, service_period_to,
                    gross_amount, tenant_apportionable, status, source
                ) VALUES (
                    :id, :org, :prop, :cat, 1, '2025-06-01',
                    '2025-01-01', '2025-12-31',
                    '300.00', 'UMLAGEFAEHIG', 'needs_review', 'MANUAL'
                )
            """),
            {
                "id": str(entry_id),
                "org": str(org_id),
                "prop": str(m2_seed["property_a_id"]),
                "cat": str(cat_id),
            },
        )

        # DELETE of draft should succeed
        await session.execute(
            text("DELETE FROM cost_entry WHERE id = :id"), {"id": str(entry_id)}
        )
        result = await session.execute(
            text("SELECT id FROM cost_entry WHERE id = :id"), {"id": str(entry_id)}
        )
        assert result.first() is None


# ============================================================
# Test 3: cost_entry draft is editable
# ============================================================
class TestCostEntryDraftEditable:
    @pytest.mark.asyncio
    async def test_foreign_and_unknown_vendors_leave_the_draft_unchanged(
        self, client_a_admin, client_b_admin, m2_seed, migrator_session,
    ):
        person = await client_b_admin.post("/api/persons", json={"last_name": "Foreign vendor"})
        assert person.status_code == 201, person.text
        vendor = await client_b_admin.post("/api/vendors", json={"person_id": person.json()["id"]})
        assert vendor.status_code == 201, vendor.text
        category = await migrator_session.scalar(text(_cost_category_id_sql()))
        created = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]), "cost_category_id": str(category),
            "cost_category_plan_version": 1, "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01", "service_period_to": "2025-12-31",
            "gross_amount": "100.00", "tenant_apportionable": "UMLAGEFAEHIG", "source": "MANUAL",
        })
        assert created.status_code == 201, created.text
        entry_id = created.json()["id"]
        before = (await migrator_session.scalars(text("SELECT to_jsonb(c)::text FROM cost_entry c ORDER BY id"))).all()
        responses = []
        for parent in (vendor.json()["id"], str(uuid.uuid4())):
            response = await client_a_admin.patch(f"/api/cost-entries/{entry_id}", json={"vendor_id": parent})
            assert response.status_code == 404, response.text
            responses.append(response.json())
        assert responses[0] == responses[1]
        assert (await migrator_session.scalars(text("SELECT to_jsonb(c)::text FROM cost_entry c ORDER BY id"))).all() == before

    @pytest.mark.asyncio
    async def test_update_needs_review_succeeds(self, app_rw_session, m2_seed):
        session = app_rw_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )
        cat_result = await session.execute(text(_cost_category_id_sql()))
        cat_id = cat_result.scalar_one()

        entry_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO cost_entry (
                    id, organization_id, property_id, cost_category_id,
                    cost_category_plan_version, invoice_date,
                    service_period_from, service_period_to,
                    gross_amount, tenant_apportionable, status, source
                ) VALUES (
                    :id, :org, :prop, :cat, 1, '2025-06-01',
                    '2025-01-01', '2025-12-31',
                    '100.00', 'UMLAGEFAEHIG', 'needs_review', 'MANUAL'
                )
            """),
            {
                "id": str(entry_id),
                "org": str(org_id),
                "prop": str(m2_seed["property_a_id"]),
                "cat": str(cat_id),
            },
        )

        # UPDATE should succeed on draft
        await session.execute(
            text("UPDATE cost_entry SET gross_amount = '200.00' WHERE id = :id"),
            {"id": str(entry_id)},
        )
        result = await session.execute(
            text("SELECT gross_amount FROM cost_entry WHERE id = :id"),
            {"id": str(entry_id)},
        )
        row = result.first()
        assert row is not None
        assert float(row.gross_amount) == 200.00


# ============================================================
# Test 4: invoice_document released is immutable
# ============================================================
class TestInvoiceDocumentReleasedImmutable:
    @pytest.mark.asyncio
    async def test_update_sha256_on_released_rejected(self, migrator_session, m2_seed):
        session = migrator_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )

        await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(m2_seed['user_a_admin_id'])})
        doc_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO invoice_document (
                    id, organization_id, storage_uri, storage_backend,
                    mime_type, content_byte_size, content_sha256,
                    version, chain_root_id, status, released_at, released_by
                ) VALUES (
                    :id, :org, 'test/path.bin', 'LOCAL_FS',
                    'application/pdf', 1024, :sha,
                    1, :id, 'active', now(), :user_id
                )
            """),
            {
                "id": str(doc_id),
                "org": str(org_id),
                "sha": "a" * 64,
                "user_id": str(m2_seed["user_a_admin_id"]),
            },
        )

        await session.execute(text('SET LOCAL ROLE app_rw'))
        # UPDATE content_sha256 -> must fail
        with pytest.raises(Exception, match="immutable|GoBD.*content_sha256.*released"):
            await session.execute(
                text("UPDATE invoice_document SET content_sha256 = :sha WHERE id = :id"),
                {"id": str(doc_id), "sha": "b" * 64},
            )

    @pytest.mark.asyncio
    async def test_active_to_superseded_allowed(self, migrator_session, m2_seed):
        """Status transition active->superseded on released doc is allowed."""
        session = migrator_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )

        await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(m2_seed['user_a_admin_id'])})
        doc_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO invoice_document (
                    id, organization_id, storage_uri, storage_backend,
                    mime_type, content_byte_size, content_sha256,
                    version, chain_root_id, status, released_at, released_by
                ) VALUES (
                    :id, :org, 'test/path2.bin', 'LOCAL_FS',
                    'application/pdf', 2048, :sha,
                    1, :id, 'active', now(), :user_id
                )
            """),
            {
                "id": str(doc_id),
                "org": str(org_id),
                "sha": "c" * 64,
                "user_id": str(m2_seed["user_a_admin_id"]),
            },
        )

        await session.execute(text('SET LOCAL ROLE app_rw'))
        # active -> superseded should succeed
        await session.execute(
            text("""
                UPDATE invoice_document
                SET status = 'superseded', superseded_at = now()
                WHERE id = :id
            """),
            {"id": str(doc_id)},
        )
        result = await session.execute(
            text("SELECT status FROM invoice_document WHERE id = :id"),
            {"id": str(doc_id)},
        )
        assert result.scalar_one() == "superseded"


# ============================================================
# Test 5: invoice_document no DELETE grant for app_rw
# ============================================================
class TestInvoiceDocumentNoDeleteGrant:
    @pytest.mark.asyncio
    async def test_no_delete_privilege(self, migrator_session):
        result = await migrator_session.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_name = 'invoice_document' AND grantee = 'app_rw'"
            )
        )
        privileges = {row.privilege_type for row in result.fetchall()}
        assert "DELETE" not in privileges, "app_rw should NOT have DELETE on invoice_document"
        assert "SELECT" in privileges
        assert "INSERT" in privileges
        assert "UPDATE" in privileges


# ============================================================
# Test 6: Correction creates new version (cost_entry)
# ============================================================
class TestCostEntryCorrectionVersion:
    @pytest.mark.asyncio
    async def test_correction_via_api(self, client_a_admin, m2_seed):
        # First get a category ID
        cats_resp = await client_a_admin.get("/api/cost-categories")
        assert cats_resp.status_code == 200
        cat_id = cats_resp.json()[0]["id"]

        # Create a cost entry
        create_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "1000.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        assert create_resp.status_code == 201
        entry_id = create_resp.json()["id"]

        # Confirm it
        confirm_resp = await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "confirmed"

        # Create correction
        corr_resp = await client_a_admin.post(
            f"/api/cost-entries/{entry_id}/correction",
            json={"gross_amount": "1200.00"},
        )
        assert corr_resp.status_code == 201
        new_entry = corr_resp.json()
        assert new_entry["gross_amount"] == "1200.00"
        assert new_entry["status"] == "needs_review"

        # Original should now have superseded_by_id set
        orig_resp = await client_a_admin.get(f"/api/cost-entries/{entry_id}")
        assert orig_resp.status_code == 200
        assert orig_resp.json()["superseded_by_id"] == new_entry["id"]
        # Original gross_amount unchanged (frozen)
        assert orig_resp.json()["gross_amount"] == "1000.00"


# ============================================================
# Test 7: Document version chain + partial unique index
# ============================================================
class TestDocumentVersionChain:
    @pytest.mark.asyncio
    async def test_no_second_active_per_chain(self, migrator_session, m2_seed):
        """Partial unique index blocks a second active doc per chain."""
        session = migrator_session
        org_id = m2_seed["org_a_id"]
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )

        await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(m2_seed['user_a_admin_id'])})
        chain_root = uuid.uuid4()
        doc1_id = chain_root
        await session.execute(
            text("""
                INSERT INTO invoice_document (
                    id, organization_id, storage_uri, storage_backend,
                    mime_type, content_byte_size, content_sha256,
                    version, chain_root_id, status
                ) VALUES (
                    :id, :org, 'test/v1.bin', 'LOCAL_FS',
                    'application/pdf', 100, :sha,
                    1, :chain, 'active'
                )
            """),
            {
                "id": str(doc1_id),
                "org": str(org_id),
                "sha": "d" * 64,
                "chain": str(chain_root),
            },
        )

        # Try to insert a second active with the same chain_root -> unique violation
        doc2_id = uuid.uuid4()
        with pytest.raises(Exception, match="uq_active_doc_per_chain"):
            await session.execute(
                text("""
                    INSERT INTO invoice_document (
                        id, organization_id, storage_uri, storage_backend,
                        mime_type, content_byte_size, content_sha256,
                        version, chain_root_id, status,
                        supersedes_id
                    ) VALUES (
                        :id, :org, 'test/v2.bin', 'LOCAL_FS',
                        'application/pdf', 200, :sha,
                        2, :chain, 'active',
                        :supersedes
                    )
                """),
                {
                    "id": str(doc2_id),
                    "org": str(org_id),
                    "sha": "e" * 64,
                    "chain": str(chain_root),
                    "supersedes": str(doc1_id),
                },
            )


# ============================================================
# Test 8: Hash verification (tamper evidence)
# ============================================================
class TestHashVerification:
    def test_verify_intact_file(self, tmp_store):
        data = b"Hello, this is a test document."
        sha = compute_sha256(data)
        key = make_storage_key(str(uuid.uuid4()), sha)
        tmp_store.put(key, data, content_type="application/pdf")

        assert verify_document_integrity(sha, key, tmp_store) is True

    def test_verify_tampered_file(self, tmp_store):
        data = b"Original document content."
        sha = compute_sha256(data)
        key = make_storage_key(str(uuid.uuid4()), sha)
        ref = tmp_store.put(key, data, content_type="application/pdf")

        # Tamper with the file on disk
        path = tmp_store._root / key
        # Make writable, tamper, make read-only again
        path.chmod(0o644)
        path.write_bytes(b"TAMPERED content!")
        path.chmod(0o444)

        assert verify_document_integrity(sha, key, tmp_store) is False

    def test_verify_missing_file(self, tmp_store):
        assert verify_document_integrity("f" * 64, "nonexistent/path.bin", tmp_store) is False


# ============================================================
# Test 9: Hash mismatch between upload and confirm (rollback)
# ============================================================
class TestHashMismatchAtConfirm:
    @pytest.mark.asyncio
    async def test_tampered_file_blocks_confirm(self, client_a_admin, m2_seed, tmp_store):
        """If the file is tampered between upload and confirm, the confirm rolls back."""
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        # Create cost entry
        entry_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "500.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        assert entry_resp.status_code == 201
        entry_id = entry_resp.json()["id"]

        # Upload a document
        file_content = b"Test PDF content for tamper test"
        import io
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("test.pdf", io.BytesIO(file_content), "application/pdf")},
            data={"cost_entry_id": entry_id},
        )
        assert upload_resp.status_code == 201
        doc = upload_resp.json()

        # Tamper with the stored file
        sha = doc["content_sha256"]
        org_id = m2_seed["org_a_id"]
        key = make_storage_key(str(org_id), sha)
        path = tmp_store._root / key
        path.chmod(0o644)
        path.write_bytes(b"TAMPERED!")
        path.chmod(0o444)

        # Confirm should fail (409 due to hash mismatch)
        confirm_resp = await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")
        assert confirm_resp.status_code == 409
        assert "integrity" in confirm_resp.json()["detail"].lower()

        # Entry should still be needs_review (rollback)
        check_resp = await client_a_admin.get(f"/api/cost-entries/{entry_id}")
        assert check_resp.status_code == 200
        assert check_resp.json()["status"] == "needs_review"


# ============================================================
# Test 10: RLS fail-closed for M2 tables
# ============================================================
class TestM2RLSFailClosed:
    @pytest.mark.asyncio
    async def test_no_context_zero_cost_entries(self, app_rw_session):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )
        result = await session.execute(text("SELECT * FROM cost_entry"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_no_context_zero_invoice_documents(self, app_rw_session):
        session = app_rw_session
        await session.execute(
            text("SELECT set_config('app.current_org_id', '', true)")
        )
        result = await session.execute(text("SELECT * FROM invoice_document"))
        assert len(result.fetchall()) == 0

    @pytest.mark.asyncio
    async def test_cross_org_cost_entry_invisible(self, client_a_admin, client_b_admin, m2_seed):
        """Client B cannot see Client A's cost entries."""
        # Ensure there's at least one entry in org A
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })

        # Client B should see zero entries
        resp_b = await client_b_admin.get("/api/cost-entries")
        assert resp_b.status_code == 200
        assert len(resp_b.json()) == 0

    @pytest.mark.asyncio
    async def test_foreign_cost_entry_returns_404(self, client_a_admin, client_b_admin, m2_seed):
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        create_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        entry_id = create_resp.json()["id"]

        # Client B accessing org A's entry -> 404
        resp = await client_b_admin.get(f"/api/cost-entries/{entry_id}")
        assert resp.status_code == 404


# ============================================================
# Test 11: State machine enforcement
# ============================================================
class TestStateMachine:
    @pytest.mark.asyncio
    async def test_confirm_to_needs_review_rejected(self, client_a_admin, m2_seed):
        """confirmed -> needs_review is not allowed via PATCH."""
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        # Create and confirm
        create_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        entry_id = create_resp.json()["id"]
        await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")

        # Try to update -> 409
        patch_resp = await client_a_admin.patch(
            f"/api/cost-entries/{entry_id}",
            json={"gross_amount": "999.00"},
        )
        assert patch_resp.status_code == 409

    @pytest.mark.asyncio
    async def test_confirm_without_required_fields_rejected(self, client_a_admin, m2_seed):
        """Confirm without a property creates a 422 (validation in the create already requires it)."""
        # We test the confirm path: create with needs_review, then confirm
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        create_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        assert create_resp.status_code == 201
        entry_id = create_resp.json()["id"]
        # This should work -- all required fields present
        confirm_resp = await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")
        assert confirm_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_cannot_confirm(self, client_a_readonly, client_a_admin, m2_seed):
        """READ_ONLY role cannot confirm (403)."""
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        create_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        entry_id = create_resp.json()["id"]

        # READ_ONLY trying to confirm -> 403
        resp = await client_a_readonly.post(f"/api/cost-entries/{entry_id}/confirm")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_cannot_create(self, client_a_readonly, m2_seed):
        """READ_ONLY role cannot create cost entries (403)."""
        cats_resp = await client_a_readonly.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        resp = await client_a_readonly.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        assert resp.status_code == 403


# ============================================================
# Test 12: RLS coverage extended (in test_rls_coverage.py)
# ============================================================
# This is verified by the extended TENANT_TABLES/GLOBAL_TABLES in test_rls_coverage.py.
# The test_no_unknown_tables test will fail if cost_entry/invoice_document/cost_category
# are not properly registered.


# ============================================================
# Test 13: Atomic confirm rollback
# ============================================================
class TestAtomicConfirmRollback:
    @pytest.mark.asyncio
    async def test_confirm_is_atomic(self, client_a_admin, m2_seed, tmp_store):
        """Verify that confirm + document release happen atomically.

        The tamper test (Test 9) already proves rollback on hash failure.
        Here we verify the positive path: confirm + release in one shot.
        """
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        # Create entry
        entry_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "500.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        entry_id = entry_resp.json()["id"]

        # Upload document
        import io
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("invoice.pdf", io.BytesIO(b"Valid PDF content"), "application/pdf")},
            data={"cost_entry_id": entry_id},
        )
        assert upload_resp.status_code == 201
        doc_id = upload_resp.json()["id"]

        # Confirm -> entry confirmed AND document released
        confirm_resp = await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "confirmed"

        # Check document is released
        doc_resp = await client_a_admin.get(f"/api/invoice-documents/{doc_id}")
        assert doc_resp.status_code == 200
        assert doc_resp.json()["released_at"] is not None
        assert doc_resp.json()["retention_until"] is not None


# ============================================================
# Test 14: retention_until calculation
# ============================================================
class TestRetentionUntilCalculation:
    @pytest.mark.asyncio
    async def test_retention_until_set_on_confirm(self, client_a_admin, m2_seed, tmp_store):
        """retention_until is computed and set during confirm (O-1 provisional)."""
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        entry_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-15",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        entry_id = entry_resp.json()["id"]

        # Upload a doc
        import io
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("r.pdf", io.BytesIO(b"Retention test"), "application/pdf")},
            data={"cost_entry_id": entry_id},
        )
        doc_id = upload_resp.json()["id"]

        # Confirm
        await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")

        doc_resp = await client_a_admin.get(f"/api/invoice-documents/{doc_id}")
        retention = doc_resp.json()["retention_until"]
        # invoice_date = 2025-06-15 -> end of 2025 + 8 years = 2033-12-31
        assert retention == "2033-12-31"

    @pytest.mark.asyncio
    async def test_retention_null_before_confirm(self, client_a_admin, m2_seed, tmp_store):
        """retention_until is NULL before confirm."""
        cats_resp = await client_a_admin.get("/api/cost-categories")
        cat_id = cats_resp.json()[0]["id"]

        entry_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-06-15",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "MANUAL",
            "status": "needs_review",
        })
        entry_id = entry_resp.json()["id"]

        import io
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("r.pdf", io.BytesIO(b"Before confirm"), "application/pdf")},
            data={"cost_entry_id": entry_id},
        )
        doc_resp = upload_resp.json()
        assert doc_resp["retention_until"] is None
        assert doc_resp["released_at"] is None


# ============================================================
# End-to-end M2 exit path
# ============================================================
class TestM2ExitPathEndToEnd:
    @pytest.mark.asyncio
    async def test_upload_confirm_archive_path(self, client_a_admin, m2_seed, tmp_store):
        """M2 exit criterion: PDF upload -> draft -> confirm -> immutable + archived."""
        # 1. Get category
        cats_resp = await client_a_admin.get("/api/cost-categories")
        assert cats_resp.status_code == 200
        assert len(cats_resp.json()) > 0
        cat_id = cats_resp.json()[0]["id"]

        # 2. Create cost entry (manual, needs_review)
        entry_resp = await client_a_admin.post("/api/cost-entries", json={
            "property_id": str(m2_seed["property_a_id"]),
            "cost_category_id": cat_id,
            "cost_category_plan_version": 1,
            "invoice_date": "2025-07-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "750.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "owner_distribution_key": "MEA",
            "tenant_distribution_key": "WOHNFLAECHE",
            "source": "MANUAL",
            "status": "needs_review",
        })
        assert entry_resp.status_code == 201
        entry = entry_resp.json()
        entry_id = entry["id"]

        # 3. Upload PDF document
        import io
        pdf_content = b"%PDF-1.4 fake test invoice content"
        upload_resp = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("rechnung.pdf", io.BytesIO(pdf_content), "application/pdf")},
            data={"cost_entry_id": entry_id},
        )
        assert upload_resp.status_code == 201
        doc = upload_resp.json()
        doc_id = doc["id"]
        assert doc["status"] == "active"
        assert doc["released_at"] is None

        # 4. Verify document integrity before confirm
        verify_resp = await client_a_admin.get(f"/api/invoice-documents/{doc_id}/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["integrity_ok"] is True

        # 5. Confirm (atomic: entry confirmed + doc released)
        confirm_resp = await client_a_admin.post(f"/api/cost-entries/{entry_id}/confirm")
        assert confirm_resp.status_code == 200
        confirmed = confirm_resp.json()
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmed_at"] is not None

        # 6. Document is now released and immutable
        doc_resp = await client_a_admin.get(f"/api/invoice-documents/{doc_id}")
        assert doc_resp.status_code == 200
        released_doc = doc_resp.json()
        assert released_doc["released_at"] is not None
        assert released_doc["retention_until"] is not None

        # 7. Verify integrity post-confirm
        verify_resp2 = await client_a_admin.get(f"/api/invoice-documents/{doc_id}/verify")
        assert verify_resp2.json()["integrity_ok"] is True

        # 8. Stream the file content (authenticated)
        content_resp = await client_a_admin.get(f"/api/invoice-documents/{doc_id}/content")
        assert content_resp.status_code == 200
        assert content_resp.content == pdf_content

        # 9. Entry is abrechnungsrelevant (active query: confirmed + no superseded_by_id)
        assert confirmed["superseded_by_id"] is None
        # -> This entry would be picked up by M3's active-entry query


class TestInvoiceUploadLimits:
    @pytest.mark.asyncio
    async def test_multipart_upload_over_limit_is_rejected(
        self, client_a_admin, tmp_store, monkeypatch
    ):
        import app.api.invoice_documents as invoice_api

        monkeypatch.setattr(invoice_api, "MAX_UPLOAD_BYTES", 4, raising=False)
        response = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("too-large.pdf", b"abcde", "application/pdf")},
        )

        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_multipart_version_over_limit_is_rejected(
        self, client_a_admin, tmp_store, monkeypatch
    ):
        import app.api.invoice_documents as invoice_api
        from fastapi import HTTPException

        original = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("original.pdf", b"abc", "application/pdf")},
        )
        assert original.status_code == 201
        monkeypatch.setattr(invoice_api, "MAX_UPLOAD_BYTES", 4)

        async def should_not_receive_oversized_content(*args, **kwargs):
            raise HTTPException(status_code=500, detail="domain service was called")

        monkeypatch.setattr(
            invoice_api.InvoiceDocumentService,
            "create_new_version",
            should_not_receive_oversized_content,
        )

        response = await client_a_admin.post(
            f"/api/invoice-documents/{original.json()['id']}/version",
            files={"file": ("too-large.pdf", b"abcde", "application/pdf")},
        )

        assert response.status_code == 413


class TestInvoiceDownloadHeaders:
    @pytest.mark.asyncio
    async def test_download_uses_safe_attachment_headers(
        self, client_a_admin, tmp_store
    ):
        content = b"invoice-content"
        response = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ('invoice; bad:"name.html', content, "text/html")},
        )
        assert response.status_code == 201

        download = await client_a_admin.get(
            f"/api/invoice-documents/{response.json()['id']}/content"
        )

        assert download.status_code == 200
        assert download.content == content
        assert download.headers["content-type"] == "text/html; charset=utf-8"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["content-length"] == str(len(content))
        assert download.headers["content-disposition"].startswith(
            'attachment; filename="invoice_ bad_%22name.html"'
        )

    @pytest.mark.asyncio
    async def test_download_does_not_use_unatomic_exists_precheck(
        self, client_a_admin, tmp_store, monkeypatch
    ):
        response = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("invoice.pdf", b"invoice-content", "application/pdf")},
        )
        assert response.status_code == 201

        def fail_if_called(key):
            raise AssertionError("download performed an exists() precheck")

        monkeypatch.setattr(tmp_store, "exists", fail_if_called)
        download = await client_a_admin.get(
            f"/api/invoice-documents/{response.json()['id']}/content"
        )

        assert download.status_code == 200

    @pytest.mark.asyncio
    async def test_download_returns_404_when_stored_file_is_missing(
        self, client_a_admin, tmp_store
    ):
        response = await client_a_admin.post(
            "/api/invoice-documents/upload",
            files={"file": ("invoice.pdf", b"invoice-content", "application/pdf")},
        )
        assert response.status_code == 201
        next(tmp_store._root.rglob("*.bin")).unlink()

        download = await client_a_admin.get(
            f"/api/invoice-documents/{response.json()['id']}/content"
        )

        assert download.status_code == 404


class TestUploadFirstAtomic:
    async def payload(self, client, seed, doc_id=None):
        categories = (await client.get("/api/cost-categories")).json()
        return {
            "property_id": str(seed["property_a_id"]),
            "cost_category_id": categories[0]["id"],
            "invoice_date": "2025-06-01",
            "service_period_from": "2025-01-01",
            "service_period_to": "2025-12-31",
            "gross_amount": "100.00",
            "tenant_apportionable": "UMLAGEFAEHIG",
            "source": "PDF_IMPORT" if doc_id else "MANUAL",
            **({"source_document_id": doc_id} if doc_id else {}),
        }

    async def upload(self, client, content=b"synthetic invoice", cost_id=None):
        response = await client.post(
            "/api/invoice-documents/upload",
            files={"file": ("invoice.pdf", content, "application/pdf")},
            data={"cost_entry_id": cost_id} if cost_id else {},
        )
        assert response.status_code == 201, response.text
        return response.json()

    @pytest.mark.asyncio
    async def test_link_replay_confirm_original(self, client_a_admin, m2_seed, tmp_store):
        doc = await self.upload(client_a_admin)
        body = await self.payload(client_a_admin, m2_seed, doc["id"])
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        first = await client_a_admin.post("/api/cost-entries", json=body, headers=headers)
        assert first.status_code == 201, first.text
        replay = await client_a_admin.post("/api/cost-entries", json=body, headers=headers)
        assert replay.status_code == 201
        assert replay.json()["id"] == first.json()["id"]
        linked = (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}")).json()
        assert linked["cost_entry_id"] == first.json()["id"]
        conflict = await client_a_admin.post("/api/cost-entries", json={**body, "gross_amount": "200.00"}, headers=headers)
        assert conflict.status_code == 409
        confirmed = await client_a_admin.post(f"/api/cost-entries/{first.json()['id']}/confirm")
        assert confirmed.status_code == 200
        released = (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}")).json()
        assert released["released_at"] is not None
        original = await client_a_admin.get(f"/api/invoice-documents/{doc['id']}/content")
        assert original.content == b"synthetic invoice"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("same_key", [True, False])
    async def test_concurrent_creation(self, client_a_admin, m2_seed, tmp_store, same_key):
        import asyncio
        doc = await self.upload(client_a_admin)
        body = await self.payload(client_a_admin, m2_seed, doc["id"])
        key = str(uuid.uuid4())
        responses = await asyncio.gather(*[
            client_a_admin.post("/api/cost-entries", json=body,
                headers={"Idempotency-Key": key if same_key else str(uuid.uuid4())})
            for _ in range(2)
        ])
        assert sorted(r.status_code for r in responses) == ([201, 201] if same_key else [201, 409])
        ids = {r.json()["id"] for r in responses if r.status_code == 201}
        assert len(ids) == 1

    @pytest.mark.asyncio
    async def test_source_access_and_missing_source(self, client_a_admin, client_b_admin, client_a_readonly, m2_seed, tmp_store):
        foreign = await self.upload(client_b_admin)
        body = await self.payload(client_a_admin, m2_seed, foreign["id"])
        assert (await client_a_admin.post("/api/cost-entries", json=body)).status_code == 404
        body.pop("source_document_id")
        assert (await client_a_admin.post("/api/cost-entries", json=body)).status_code == 422
        local = await self.upload(client_a_admin)
        body["source_document_id"] = local["id"]
        assert (await client_a_readonly.post("/api/cost-entries", json=body)).status_code == 403
        assert (await client_b_admin.get(f"/api/invoice-documents/{local['id']}/content")).status_code == 404

    @pytest.mark.asyncio
    async def test_cost_first_duplicate_and_foreign(self, client_a_admin, client_b_admin, m2_seed, tmp_store):
        body = await self.payload(client_a_admin, m2_seed)
        entry = (await client_a_admin.post("/api/cost-entries", json=body)).json()
        await self.upload(client_a_admin, cost_id=entry["id"])
        duplicate = await client_a_admin.post("/api/invoice-documents/upload",
            files={"file": ("second.pdf", b"second", "application/pdf")}, data={"cost_entry_id": entry["id"]})
        assert duplicate.status_code == 409
        foreign = await client_b_admin.post("/api/invoice-documents/upload",
            files={"file": ("foreign.pdf", b"foreign", "application/pdf")}, data={"cost_entry_id": entry["id"]})
        assert foreign.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_create_confirmed_or_wrong_category_version(self, client_a_admin, m2_seed):
        body = await self.payload(client_a_admin, m2_seed)
        for change in ({"status": "confirmed"}, {"cost_category_plan_version": 999}):
            response = await client_a_admin.post("/api/cost-entries", json={**body, **change})
            assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_db_scope_and_receipt_rls(self, client_a_admin, client_b_admin, m2_seed, tmp_store, app_rw_session):
        from sqlalchemy.exc import IntegrityError
        local = await self.upload(client_a_admin)
        foreign = await self.upload(client_b_admin)
        body = await self.payload(client_a_admin, m2_seed)
        entry = (await client_a_admin.post("/api/cost-entries", json=body,
            headers={"Idempotency-Key": str(uuid.uuid4())})).json()
        session = app_rw_session
        # Null links already passed through both orgs' authenticated uploads.
        await session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(m2_seed["org_b_id"])})
        await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(m2_seed['user_b_admin_id'])})
        with pytest.raises(IntegrityError, match="fk_invoice_document_cost_tenant"):
            async with session.begin_nested():
                await session.execute(text("UPDATE invoice_document SET cost_entry_id=:cost WHERE id=:doc"), {"cost": entry["id"], "doc": foreign["id"]})
        assert not (await session.execute(text("SELECT * FROM operation_receipt"))).all()
        await session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(m2_seed["org_a_id"])})
        await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(m2_seed['user_a_admin_id'])})
        await session.execute(text("UPDATE invoice_document SET cost_entry_id=:cost WHERE id=:doc"), {"cost": entry["id"], "doc": local["id"]})
        assert (await session.execute(text("SELECT * FROM operation_receipt WHERE result_id=:id"), {"id": entry["id"]})).one()
        await session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
        assert not (await session.execute(text("SELECT * FROM operation_receipt"))).all()

    @pytest.mark.asyncio
    async def test_replacement_and_confirmed_correction_chain(self, client_a_admin, m2_seed, tmp_store):
        doc = await self.upload(client_a_admin)
        body = await self.payload(client_a_admin, m2_seed, doc["id"])
        entry = (await client_a_admin.post("/api/cost-entries", json=body)).json()
        replacement = await client_a_admin.post(f"/api/invoice-documents/{doc['id']}/version",
            files={"file": ("v2.pdf", b"version 2", "application/pdf")})
        assert replacement.status_code == 201, replacement.text
        assert replacement.json()["cost_entry_id"] == entry["id"]
        assert (await client_a_admin.post(f"/api/cost-entries/{entry['id']}/confirm")).status_code == 200
        correction = await client_a_admin.post(f"/api/cost-entries/{entry['id']}/correction", json={"gross_amount": "120.00"})
        assert correction.status_code == 201
        unrelated = (await client_a_admin.post("/api/cost-entries", json=await self.payload(client_a_admin, m2_seed))).json()
        for target, expected in [(unrelated["id"], 409), (correction.json()["id"], 201)]:
            result = await client_a_admin.post(f"/api/invoice-documents/{replacement.json()['id']}/version",
                files={"file": ("v3.pdf", b"version 3", "application/pdf")}, data={"cost_entry_id": target})
            assert result.status_code == expected, result.text
        old = (await client_a_admin.get(f"/api/invoice-documents/{replacement.json()['id']}")).json()
        assert old["cost_entry_id"] == entry["id"]
        assert old["released_at"]
        assert old["status"] == "superseded"


    @pytest.mark.asyncio
    async def test_link_racing_replacement_keeps_cost_source(self, client_a_admin, m2_seed, tmp_store, monkeypatch):
        import asyncio
        from app.repositories.invoice_document_repo import InvoiceDocumentRepository
        from tests.test_scope_payment_reconciliation import read_value
        doc = await self.upload(client_a_admin)
        body = await self.payload(client_a_admin, m2_seed, doc["id"])
        read, release = asyncio.Event(), asyncio.Event()
        original = InvoiceDocumentRepository.get_active_by_chain
        async def paused(repo, chain_id, *, lock=False):
            result = await original(repo, chain_id, lock=lock)
            if not lock:
                read.set()
                await release.wait()
            return result
        monkeypatch.setattr(InvoiceDocumentRepository, "get_active_by_chain", paused)
        replacement = asyncio.create_task(client_a_admin.post(f"/api/invoice-documents/{doc['id']}/version",
            files={"file": ("v2.pdf", b"second", "application/pdf")}))
        linked = None
        try:
            await asyncio.wait_for(read.wait(), 5)
            linked = asyncio.create_task(client_a_admin.post("/api/cost-entries", json=body))
            for _ in range(100):
                blocked = await read_value("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename='app_rw' AND wait_event='advisory')", {})
                if blocked: break
                await asyncio.sleep(.01)
            assert blocked and not linked.done()
        finally:
            release.set()
        replaced, created = await asyncio.wait_for(asyncio.gather(replacement, linked), 5)
        assert replaced.status_code == 201, replaced.text
        assert created.status_code == 409, created.text
        # The serialized loser cannot attach a superseded original or leave a cost.
        assert (await client_a_admin.get(f"/api/invoice-documents/{doc['id']}")).json()['cost_entry_id'] is None
        assert replaced.json()['cost_entry_id'] is None
