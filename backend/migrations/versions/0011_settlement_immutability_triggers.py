"""M3: settlement immutability triggers (ADR-0003).

Status-aware BEFORE UPDATE/DELETE triggers on settlement_run and
settlement_line -- the DB-level enforcement of "frozen once finalized".
Same mechanic as migration 0008 (cost_entry / invoice_document); NOTHING new
is invented -- ADR-0003 section 3 had already earmarked these two tables for
"the same trigger mechanic with freeze condition status='finalized'".

settlement_run (freeze condition: OLD.status = 'finalized'):
  - UPDATE of any content-bearing column on a finalized run -> RAISE EXCEPTION.
  - The ONLY allowed change on a finalized run is setting superseded_by_id
    (the Storno/Neuberechnung correction chain, section 1.6) + updated_at.
  - DELETE of a finalized run -> always RAISE EXCEPTION.
  - draft runs are freely editable/deletable (recompute replaces them).

settlement_line (freeze condition: the PARENT settlement_run is finalized):
  - ANY UPDATE/DELETE of a line whose parent run is finalized -> RAISE
    EXCEPTION (a finalized statement has no "allowed" line mutation; a
    correction is a NEW run). Looked up via SELECT status FROM settlement_run.
  - While the parent run is draft, lines are free (recompute deletes/re-writes).

Both tables keep a full CRUD grant (editable draft); the trigger is the sole
status-dependent barrier (decision E3-5, section 1.6).

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ======================================================================
    # settlement_run immutability trigger (ADR-0003)
    # ======================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_settlement_run_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD/Snapshot immutability for settlement_run (ADR-0003).
    --
    -- FREEZE CONDITION: OLD.status = 'finalized'
    --
    -- Once a run is finalized, its content-bearing columns AND its snapshot
    -- (snapshot_payload + snapshot_sha256) are frozen. The ONLY allowed
    -- mutation is setting superseded_by_id (linking the Storno/Neuberechnung
    -- successor, section 1.6).
    --
    -- Content-bearing columns (frozen once finalized):
    --   organization_id, property_id, management_mandate_id, run_type,
    --   period_start, period_end, unit_id, lease_agreement_id,
    --   tenant_profile_id, advance_payments_total, advance_payments_source,
    --   total_cost, balance, status (cannot move away from finalized --
    --   correction = new run), snapshot_payload, snapshot_sha256,
    --   engine_version, finalized_at, finalized_by
    --
    -- Allowed changes on a finalized run:
    --   superseded_by_id (NULL -> a valid UUID, correction chain)
    --   updated_at (technical timestamp)
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'finalized' THEN
                RAISE EXCEPTION 'GoBD: DELETE of finalized settlement_run is forbidden (id=%)', OLD.id;
            END IF;
            -- drafts: DELETE allowed (recompute / discard)
            RETURN OLD;
        END IF;

        -- UPDATE path
        IF OLD.status = 'finalized' THEN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change organization_id on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.property_id IS DISTINCT FROM OLD.property_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change property_id on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.management_mandate_id IS DISTINCT FROM OLD.management_mandate_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change management_mandate_id on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.run_type IS DISTINCT FROM OLD.run_type THEN
                RAISE EXCEPTION 'GoBD: Cannot change run_type on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.period_start IS DISTINCT FROM OLD.period_start THEN
                RAISE EXCEPTION 'GoBD: Cannot change period_start on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.period_end IS DISTINCT FROM OLD.period_end THEN
                RAISE EXCEPTION 'GoBD: Cannot change period_end on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.unit_id IS DISTINCT FROM OLD.unit_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change unit_id on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.lease_agreement_id IS DISTINCT FROM OLD.lease_agreement_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change lease_agreement_id on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.tenant_profile_id IS DISTINCT FROM OLD.tenant_profile_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change tenant_profile_id on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.advance_payments_total IS DISTINCT FROM OLD.advance_payments_total THEN
                RAISE EXCEPTION 'GoBD: Cannot change advance_payments_total on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.advance_payments_source IS DISTINCT FROM OLD.advance_payments_source THEN
                RAISE EXCEPTION 'GoBD: Cannot change advance_payments_source on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.total_cost IS DISTINCT FROM OLD.total_cost THEN
                RAISE EXCEPTION 'GoBD: Cannot change total_cost on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.balance IS DISTINCT FROM OLD.balance THEN
                RAISE EXCEPTION 'GoBD: Cannot change balance on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'GoBD: Cannot change status on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.snapshot_payload IS DISTINCT FROM OLD.snapshot_payload THEN
                RAISE EXCEPTION 'GoBD: Cannot change snapshot_payload on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.snapshot_sha256 IS DISTINCT FROM OLD.snapshot_sha256 THEN
                RAISE EXCEPTION 'GoBD: Cannot change snapshot_sha256 on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.engine_version IS DISTINCT FROM OLD.engine_version THEN
                RAISE EXCEPTION 'GoBD: Cannot change engine_version on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.finalized_at IS DISTINCT FROM OLD.finalized_at THEN
                RAISE EXCEPTION 'GoBD: Cannot change finalized_at on finalized settlement_run (id=%)', OLD.id;
            END IF;
            IF NEW.finalized_by IS DISTINCT FROM OLD.finalized_by THEN
                RAISE EXCEPTION 'GoBD: Cannot change finalized_by on finalized settlement_run (id=%)', OLD.id;
            END IF;
            -- superseded_by_id: this IS the allowed change (correction chain)
            -- updated_at: technical timestamp, allowed
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_settlement_run_immutability
        BEFORE UPDATE OR DELETE ON settlement_run
        FOR EACH ROW
        EXECUTE FUNCTION trg_settlement_run_immutability();
    """)

    # ======================================================================
    # settlement_line immutability trigger (ADR-0003)
    # ======================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_settlement_line_immutability()
    RETURNS TRIGGER AS $$
    -- Snapshot immutability for settlement_line (ADR-0003).
    --
    -- FREEZE CONDITION: the PARENT settlement_run is finalized.
    --
    -- A finalized statement's lines are part of the frozen, hashed result;
    -- there is NO allowed line mutation (a correction is a new run). While the
    -- parent run is draft, lines are free (recompute deletes/re-writes them).
    --
    -- We look up the parent run's status. On DELETE we use OLD; on UPDATE we
    -- guard against re-pointing a line to a different run as well.
    DECLARE
        parent_status TEXT;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            SELECT status INTO parent_status FROM settlement_run WHERE id = OLD.settlement_run_id;
            IF parent_status = 'finalized' THEN
                RAISE EXCEPTION 'GoBD: DELETE of settlement_line on a finalized run is forbidden (line id=%, run id=%)', OLD.id, OLD.settlement_run_id;
            END IF;
            RETURN OLD;
        END IF;

        -- UPDATE path: block if EITHER the old or the (re-pointed) new parent
        -- run is finalized.
        SELECT status INTO parent_status FROM settlement_run WHERE id = OLD.settlement_run_id;
        IF parent_status = 'finalized' THEN
            RAISE EXCEPTION 'GoBD: Cannot modify settlement_line on a finalized run (line id=%, run id=%)', OLD.id, OLD.settlement_run_id;
        END IF;
        IF NEW.settlement_run_id IS DISTINCT FROM OLD.settlement_run_id THEN
            SELECT status INTO parent_status FROM settlement_run WHERE id = NEW.settlement_run_id;
            IF parent_status = 'finalized' THEN
                RAISE EXCEPTION 'GoBD: Cannot attach settlement_line to a finalized run (line id=%, run id=%)', OLD.id, NEW.settlement_run_id;
            END IF;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_settlement_line_immutability
        BEFORE UPDATE OR DELETE ON settlement_line
        FOR EACH ROW
        EXECUTE FUNCTION trg_settlement_line_immutability();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_line_immutability ON settlement_line")
    op.execute("DROP FUNCTION IF EXISTS trg_settlement_line_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_run_immutability ON settlement_run")
    op.execute("DROP FUNCTION IF EXISTS trg_settlement_run_immutability()")
