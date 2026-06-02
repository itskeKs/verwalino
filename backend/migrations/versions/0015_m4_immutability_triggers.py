"""M4: economic_plan + economic_plan_line immutability triggers (ADR-0003).

Status-aware BEFORE UPDATE/DELETE triggers -- the DB-level enforcement of
"frozen once finalized". Same mechanic as migrations 0008/0011; nothing new is
invented -- a finalized Wirtschaftsplan is a beschlossenes Dokument that must
stay frozen.

economic_plan (freeze condition: OLD.status = 'finalized'):
  - UPDATE of any content-bearing column on a finalized plan -> RAISE EXCEPTION.
  - The ONLY allowed change on a finalized plan is setting superseded_by_id
    (the correction chain, E-4) + updated_at.
  - DELETE of a finalized plan -> always RAISE EXCEPTION.
  - draft plans are freely editable/deletable (recompute replaces them).

economic_plan_line (freeze condition: the PARENT economic_plan is finalized):
  - ANY UPDATE/DELETE of a line whose parent plan is finalized -> RAISE
    EXCEPTION (a correction is a NEW plan). While the parent is draft, lines are
    free (recompute deletes/re-writes them).

Both tables keep a full CRUD grant (editable draft); the trigger is the sole
status-dependent barrier (E3-5 line).

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ======================================================================
    # economic_plan immutability trigger (ADR-0003)
    # ======================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_economic_plan_immutability()
    RETURNS TRIGGER AS $$
    -- GoBD/Snapshot immutability for economic_plan (ADR-0003).
    --
    -- FREEZE CONDITION: OLD.status = 'finalized'
    --
    -- Once a plan is finalized, its content-bearing columns AND its snapshot
    -- (snapshot_payload + snapshot_sha256) are frozen. The ONLY allowed mutation
    -- is setting superseded_by_id (linking the correction successor).
    --
    -- Content-bearing columns (frozen once finalized):
    --   organization_id, property_id, management_mandate_id, plan_year,
    --   period_start, period_end, monthly_due_day, reserve_contribution_total,
    --   status (cannot move away from finalized -- correction = new plan),
    --   snapshot_payload, snapshot_sha256, engine_version, finalized_at,
    --   finalized_by
    --
    -- Allowed changes on a finalized plan:
    --   superseded_by_id (NULL -> a valid UUID, correction chain)
    --   updated_at (technical timestamp)
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status = 'finalized' THEN
                RAISE EXCEPTION 'GoBD: DELETE of finalized economic_plan is forbidden (id=%)', OLD.id;
            END IF;
            RETURN OLD;
        END IF;

        IF OLD.status = 'finalized' THEN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change organization_id on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.property_id IS DISTINCT FROM OLD.property_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change property_id on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.management_mandate_id IS DISTINCT FROM OLD.management_mandate_id THEN
                RAISE EXCEPTION 'GoBD: Cannot change management_mandate_id on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.plan_year IS DISTINCT FROM OLD.plan_year THEN
                RAISE EXCEPTION 'GoBD: Cannot change plan_year on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.period_start IS DISTINCT FROM OLD.period_start THEN
                RAISE EXCEPTION 'GoBD: Cannot change period_start on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.period_end IS DISTINCT FROM OLD.period_end THEN
                RAISE EXCEPTION 'GoBD: Cannot change period_end on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.monthly_due_day IS DISTINCT FROM OLD.monthly_due_day THEN
                RAISE EXCEPTION 'GoBD: Cannot change monthly_due_day on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.reserve_contribution_total IS DISTINCT FROM OLD.reserve_contribution_total THEN
                RAISE EXCEPTION 'GoBD: Cannot change reserve_contribution_total on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION 'GoBD: Cannot change status on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.snapshot_payload IS DISTINCT FROM OLD.snapshot_payload THEN
                RAISE EXCEPTION 'GoBD: Cannot change snapshot_payload on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.snapshot_sha256 IS DISTINCT FROM OLD.snapshot_sha256 THEN
                RAISE EXCEPTION 'GoBD: Cannot change snapshot_sha256 on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.engine_version IS DISTINCT FROM OLD.engine_version THEN
                RAISE EXCEPTION 'GoBD: Cannot change engine_version on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.finalized_at IS DISTINCT FROM OLD.finalized_at THEN
                RAISE EXCEPTION 'GoBD: Cannot change finalized_at on finalized economic_plan (id=%)', OLD.id;
            END IF;
            IF NEW.finalized_by IS DISTINCT FROM OLD.finalized_by THEN
                RAISE EXCEPTION 'GoBD: Cannot change finalized_by on finalized economic_plan (id=%)', OLD.id;
            END IF;
            -- superseded_by_id + updated_at: the allowed changes.
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_economic_plan_immutability
        BEFORE UPDATE OR DELETE ON economic_plan
        FOR EACH ROW
        EXECUTE FUNCTION trg_economic_plan_immutability();
    """)

    # ======================================================================
    # economic_plan_line immutability trigger (ADR-0003)
    # ======================================================================
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_economic_plan_line_immutability()
    RETURNS TRIGGER AS $$
    -- Snapshot immutability for economic_plan_line (ADR-0003).
    --
    -- FREEZE CONDITION: the PARENT economic_plan is finalized.
    --
    -- A finalized plan's lines are part of the frozen, hashed result; there is
    -- NO allowed line mutation (a correction is a new plan). While the parent
    -- plan is draft, lines are free (recompute deletes/re-writes them).
    DECLARE
        parent_status TEXT;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            SELECT status INTO parent_status FROM economic_plan WHERE id = OLD.economic_plan_id;
            IF parent_status = 'finalized' THEN
                RAISE EXCEPTION 'GoBD: DELETE of economic_plan_line on a finalized plan is forbidden (line id=%, plan id=%)', OLD.id, OLD.economic_plan_id;
            END IF;
            RETURN OLD;
        END IF;

        SELECT status INTO parent_status FROM economic_plan WHERE id = OLD.economic_plan_id;
        IF parent_status = 'finalized' THEN
            RAISE EXCEPTION 'GoBD: Cannot modify economic_plan_line on a finalized plan (line id=%, plan id=%)', OLD.id, OLD.economic_plan_id;
        END IF;
        IF NEW.economic_plan_id IS DISTINCT FROM OLD.economic_plan_id THEN
            SELECT status INTO parent_status FROM economic_plan WHERE id = NEW.economic_plan_id;
            IF parent_status = 'finalized' THEN
                RAISE EXCEPTION 'GoBD: Cannot attach economic_plan_line to a finalized plan (line id=%, plan id=%)', OLD.id, NEW.economic_plan_id;
            END IF;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER trg_economic_plan_line_immutability
        BEFORE UPDATE OR DELETE ON economic_plan_line
        FOR EACH ROW
        EXECUTE FUNCTION trg_economic_plan_line_immutability();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_economic_plan_line_immutability ON economic_plan_line")
    op.execute("DROP FUNCTION IF EXISTS trg_economic_plan_line_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_economic_plan_immutability ON economic_plan")
    op.execute("DROP FUNCTION IF EXISTS trg_economic_plan_immutability()")
