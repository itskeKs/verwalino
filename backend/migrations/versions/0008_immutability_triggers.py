"""M2: GoBD immutability triggers (ADR-0003).

Status-aware BEFORE UPDATE/DELETE triggers on cost_entry and invoice_document.
These are the DB-level enforcement of the "frozen once confirmed/released" rule.

cost_entry (freeze condition: status = 'confirmed'):
  - UPDATE of content-bearing columns on confirmed row -> RAISE EXCEPTION
  - Only allowed change: setting superseded_by_id (correction chain linkage)
  - DELETE of confirmed row -> always RAISE EXCEPTION
  - UPDATE/DELETE on drafts/rejected -> allowed

invoice_document (freeze condition: released_at IS NOT NULL):
  - UPDATE of content_sha256, storage_uri, storage_version, version,
    released_at, released_by, content_byte_size on released row -> RAISE EXCEPTION
  - Only allowed change: status active->superseded + superseded_at
  - DELETE of released row -> always RAISE EXCEPTION

The trigger source is extensively commented for audit/review.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ======================================================================
    # cost_entry immutability trigger (ADR-0003)
    # ======================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_cost_entry_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD immutability enforcement for cost_entry (ADR-0003).
    --
    -- FREEZE CONDITION: OLD.status = 'confirmed'
    --
    -- When a cost_entry row has status 'confirmed', its content-bearing columns
    -- are frozen. The ONLY allowed mutation is setting superseded_by_id
    -- (to link a correction version, see m2-gobd-belegfluss.md section 3.4).
    --
    -- Content-bearing columns (frozen once confirmed):
    --   gross_amount, vat_amount, vat_rate, invoice_date,
    --   service_period_from, service_period_to, cost_category_id,
    --   cost_category_plan_version, vendor_id, property_id,
    --   management_mandate_id, document_number,
    --   owner_distribution_key, tenant_distribution_key,
    --   tenant_apportionable, apportionable_tenant_amount,
    --   source, organization_id,
    --   status (cannot change away from confirmed -- correction = new row),
    --   confirmed_at, confirmed_by
    --
    -- Allowed changes on a confirmed row:
    --   superseded_by_id (from NULL to a valid UUID -- correction chain)
    --   updated_at (technical timestamp)
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'confirmed' THEN
                RAISE EXCEPTION 'GoBD: DELETE of confirmed cost_entry is forbidden (id=%)', OLD.id;
            END IF;
            -- Drafts / rejected: DELETE allowed
            RETURN OLD;
        END IF;

        -- UPDATE path
        IF OLD.status = 'confirmed' THEN
            -- Check each content-bearing column for changes
            IF NEW.gross_amount IS DISTINCT FROM OLD.gross_amount THEN
                RAISE EXCEPTION 'GoBD: Cannot change gross_amount on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.vat_amount IS DISTINCT FROM OLD.vat_amount THEN
                RAISE EXCEPTION 'GoBD: Cannot change vat_amount on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.vat_rate IS DISTINCT FROM OLD.vat_rate THEN
                RAISE EXCEPTION 'GoBD: Cannot change vat_rate on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.invoice_date IS DISTINCT FROM OLD.invoice_date THEN
                RAISE EXCEPTION 'GoBD: Cannot change invoice_date on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.service_period_from IS DISTINCT FROM OLD.service_period_from THEN
                RAISE EXCEPTION 'GoBD: Cannot change service_period_from on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.service_period_to IS DISTINCT FROM OLD.service_period_to THEN
                RAISE EXCEPTION 'GoBD: Cannot change service_period_to on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.cost_category_id IS DISTINCT FROM OLD.cost_category_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change cost_category_id on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.cost_category_plan_version IS DISTINCT FROM OLD.cost_category_plan_version THEN
                RAISE EXCEPTION 'GoBD: Cannot change cost_category_plan_version on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.vendor_id IS DISTINCT FROM OLD.vendor_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change vendor_id on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.property_id IS DISTINCT FROM OLD.property_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change property_id on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.management_mandate_id IS DISTINCT FROM OLD.management_mandate_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change management_mandate_id on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.document_number IS DISTINCT FROM OLD.document_number THEN
                RAISE EXCEPTION 'GoBD: Cannot change document_number on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.owner_distribution_key IS DISTINCT FROM OLD.owner_distribution_key THEN
                RAISE EXCEPTION 'GoBD: Cannot change owner_distribution_key on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_distribution_key IS DISTINCT FROM OLD.tenant_distribution_key THEN
                RAISE EXCEPTION 'GoBD: Cannot change tenant_distribution_key on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_apportionable IS DISTINCT FROM OLD.tenant_apportionable THEN
                RAISE EXCEPTION 'GoBD: Cannot change tenant_apportionable on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.apportionable_tenant_amount IS DISTINCT FROM OLD.apportionable_tenant_amount THEN
                RAISE EXCEPTION 'GoBD: Cannot change apportionable_tenant_amount on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.source IS DISTINCT FROM OLD.source THEN
                RAISE EXCEPTION 'GoBD: Cannot change source on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change organization_id on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'GoBD: Cannot change status on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at THEN
                RAISE EXCEPTION 'GoBD: Cannot change confirmed_at on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            IF NEW.confirmed_by IS DISTINCT FROM OLD.confirmed_by THEN
                RAISE EXCEPTION 'GoBD: Cannot change confirmed_by on confirmed cost_entry (id=%)', OLD.id;
            END IF;
            -- superseded_by_id: this IS the allowed change (correction chain)
            -- updated_at: technical timestamp, allowed
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_cost_entry_immutability
        BEFORE UPDATE OR DELETE ON cost_entry
        FOR EACH ROW
        EXECUTE FUNCTION trg_cost_entry_immutability();
    """)

    # ======================================================================
    # invoice_document immutability trigger (ADR-0003)
    # ======================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_invoice_document_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD immutability enforcement for invoice_document (ADR-0003).
    --
    -- FREEZE CONDITION: OLD.released_at IS NOT NULL
    --
    -- Once a document is released (released_at set), its content-integrity
    -- columns are frozen. The ONLY allowed mutation is the status transition
    -- active -> superseded (+ superseded_at) when a new version replaces it.
    --
    -- Frozen columns (once released):
    --   content_sha256, storage_uri, storage_version, version,
    --   released_at, released_by, content_byte_size,
    --   organization_id, mime_type, chain_root_id, cost_entry_id,
    --   original_filename, retention_until
    --
    -- Allowed changes on a released row:
    --   status: 'active' -> 'superseded' (version replacement)
    --   superseded_at: NULL -> timestamp (set when superseded)
    --   updated_at: technical timestamp
    --   extraction_payload: non-critical metadata, may be enriched post-release
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.released_at IS NOT NULL THEN
                RAISE EXCEPTION 'GoBD: DELETE of released invoice_document is forbidden (id=%)', OLD.id;
            END IF;
            -- Unreleased documents: DELETE still blocked by grant (no DELETE grant),
            -- but the trigger is not the barrier for unreleased rows.
            RETURN OLD;
        END IF;

        -- UPDATE path
        IF OLD.released_at IS NOT NULL THEN
            IF NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 THEN
                RAISE EXCEPTION 'GoBD: Cannot change content_sha256 on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.storage_uri IS DISTINCT FROM OLD.storage_uri THEN
                RAISE EXCEPTION 'GoBD: Cannot change storage_uri on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.storage_version IS DISTINCT FROM OLD.storage_version THEN
                RAISE EXCEPTION 'GoBD: Cannot change storage_version on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.version IS DISTINCT FROM OLD.version THEN
                RAISE EXCEPTION 'GoBD: Cannot change version on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.released_at IS DISTINCT FROM OLD.released_at THEN
                RAISE EXCEPTION 'GoBD: Cannot change released_at on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.released_by IS DISTINCT FROM OLD.released_by THEN
                RAISE EXCEPTION 'GoBD: Cannot change released_by on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.content_byte_size IS DISTINCT FROM OLD.content_byte_size THEN
                RAISE EXCEPTION 'GoBD: Cannot change content_byte_size on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change organization_id on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.mime_type IS DISTINCT FROM OLD.mime_type THEN
                RAISE EXCEPTION 'GoBD: Cannot change mime_type on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.chain_root_id IS DISTINCT FROM OLD.chain_root_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change chain_root_id on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.cost_entry_id IS DISTINCT FROM OLD.cost_entry_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change cost_entry_id on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.original_filename IS DISTINCT FROM OLD.original_filename THEN
                RAISE EXCEPTION 'GoBD: Cannot change original_filename on released invoice_document (id=%)', OLD.id;
            END IF;
            IF NEW.retention_until IS DISTINCT FROM OLD.retention_until THEN
                RAISE EXCEPTION 'GoBD: Cannot change retention_until on released invoice_document (id=%)', OLD.id;
            END IF;

            -- Validate the allowed status transition: active -> superseded only
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF NOT (OLD.status = 'active' AND NEW.status = 'superseded') THEN
                    RAISE EXCEPTION 'GoBD: Only active->superseded transition allowed on released invoice_document (id=%)', OLD.id;
                END IF;
            END IF;

            -- superseded_at: allowed (set when superseded)
            -- updated_at: allowed (technical timestamp)
            -- extraction_payload: allowed (non-critical enrichment)
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_invoice_document_immutability
        BEFORE UPDATE OR DELETE ON invoice_document
        FOR EACH ROW
        EXECUTE FUNCTION trg_invoice_document_immutability();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_invoice_document_immutability ON invoice_document")
    op.execute("DROP FUNCTION IF EXISTS trg_invoice_document_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_cost_entry_immutability ON cost_entry")
    op.execute("DROP FUNCTION IF EXISTS trg_cost_entry_immutability()")
