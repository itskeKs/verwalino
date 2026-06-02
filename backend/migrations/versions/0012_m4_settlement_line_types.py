"""M4: additive settlement_line.line_type CHECK extension + WEG addressee columns.

m4-weg-abrechnung.md section 3.2 / 6.5 / 1.5, section 10.2.

ADDITIVE only (the additive-migration discipline M3 established): the existing
line_type values stay valid; M4 adds the WEG/Vermoegensbericht line types and
two nullable columns. The run_type CHECK is UNCHANGED (the WEG/Vermoegensbericht
values were already reserved in migration 0009).

Changes:
  1. settlement_line.line_type: widen String(20) -> String(24) and extend the
     CHECK to the new WEG line types (OWNER_COST_TOTAL, PLAN_ADVANCE,
     ABRECHNUNGSSPITZE, RESERVE_CONTRIBUTION, RESERVE_OPENING, RESERVE_WITHDRAWAL,
     RESERVE_CLOSING, BANK_BALANCE, RECEIVABLE, PAYABLE).
  2. settlement_line.addressee_owner_profile_id (FK->owner_profile, RESTRICT,
     NULL): the per-owner addressee of a WEG line (m4 section 4.4).
  3. settlement_run.economic_plan_id (UUID, NULL): the linked Wirtschaftsplan
     for a WEG_JAHRESABRECHNUNG (m4 section 1.5). The FK constraint is added in
     0013 AFTER economic_plan exists (this migration only adds the column).
  4. The settlement_run / settlement_line immutability triggers (migration 0011)
     are updated via CREATE OR REPLACE so the new content-bearing columns are
     frozen once finalized too (ADR-0003). Additive -- same freeze condition.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The full line_type vocabulary AFTER this migration (M3 + M4).
OLD_LINE_TYPES = ("COST", "ADVANCE_PAYMENT", "BALANCE", "SUBTOTAL")
NEW_LINE_TYPES = (
    # M3 (unchanged)
    "COST",
    "ADVANCE_PAYMENT",
    "BALANCE",
    "SUBTOTAL",
    # M4 WEG-Jahresabrechnung (m4 section 3.2)
    "OWNER_COST_TOTAL",
    "PLAN_ADVANCE",
    "ABRECHNUNGSSPITZE",
    "RESERVE_CONTRIBUTION",
    "RESERVE_OPENING",
    "RESERVE_WITHDRAWAL",
    "RESERVE_CLOSING",
    # M4 Vermoegensbericht (m4 section 6.5)
    "BANK_BALANCE",
    "RECEIVABLE",
    "PAYABLE",
)


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


# The REAL constraint name in the DB. NB: migration 0009 declared the constraint
# with name="ck_settlement_line_line_type" inside create_table, but the metadata
# naming-convention (ck_%(table_name)s_%(constraint_name)s, app/models/base.py)
# treats that name= as the constraint_name TOKEN and prefixes it -> the actual
# Postgres name is the double-prefixed string below. We reference it verbatim.
_LINE_TYPE_CK = "ck_settlement_line_ck_settlement_line_line_type"


def upgrade() -> None:
    # 1. Widen line_type and extend its CHECK additively. Use RAW SQL for the
    #    CHECK so the metadata naming-convention (ck_%(table)s_%(constraint)s)
    #    does NOT double-prefix the name -- the existing constraint from 0009 is
    #    literally `ck_settlement_line_line_type` (set via an explicit name= in
    #    create_table, which bypasses the template).
    op.alter_column(
        "settlement_line",
        "line_type",
        type_=sa.String(24),
        existing_type=sa.String(20),
        existing_nullable=False,
    )
    op.execute(f"ALTER TABLE settlement_line DROP CONSTRAINT {_LINE_TYPE_CK}")
    op.execute(
        f"ALTER TABLE settlement_line ADD CONSTRAINT {_LINE_TYPE_CK} "
        f"CHECK (line_type IN ({_in_check(NEW_LINE_TYPES)}))"
    )

    # 2. settlement_line.addressee_owner_profile_id (per-owner WEG addressee).
    op.add_column(
        "settlement_line",
        sa.Column(
            "addressee_owner_profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("owner_profile.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_settlement_line_addressee_owner",
        "settlement_line",
        ["addressee_owner_profile_id"],
    )

    # 3. settlement_run.economic_plan_id (FK added in 0013 once economic_plan
    #    exists -- here we only add the column to keep the migration order clean).
    op.add_column(
        "settlement_run",
        sa.Column("economic_plan_id", UUID(as_uuid=True), nullable=True),
    )

    # 4. Update the immutability triggers so the new content-bearing columns are
    #    frozen once finalized (ADR-0003). CREATE OR REPLACE -> additive.
    op.execute(_SETTLEMENT_RUN_TRIGGER_FN)
    op.execute(_SETTLEMENT_LINE_TRIGGER_FN)


def downgrade() -> None:
    # Restore the M3 trigger functions (without the new-column guards).
    op.execute(_SETTLEMENT_RUN_TRIGGER_FN_M3)
    op.execute(_SETTLEMENT_LINE_TRIGGER_FN_M3)

    op.drop_column("settlement_run", "economic_plan_id")
    op.drop_index("ix_settlement_line_addressee_owner", table_name="settlement_line")
    op.drop_column("settlement_line", "addressee_owner_profile_id")

    op.execute(f"ALTER TABLE settlement_line DROP CONSTRAINT {_LINE_TYPE_CK}")
    # Re-add the narrower M3 CHECK as NOT VALID: a downgrade goes to an earlier
    # schema and existing M4 line types (OWNER_COST_TOTAL, ...) would otherwise
    # violate the narrowed CHECK. NOT VALID applies the constraint to future rows
    # without failing on legacy M4 rows (which a real downgrade would discard
    # anyway). This keeps the downgrade reversible; the test harness rebuilds the
    # schema via downgrade->base, so no stale data survives.
    op.execute(
        f"ALTER TABLE settlement_line ADD CONSTRAINT {_LINE_TYPE_CK} "
        f"CHECK (line_type IN ({_in_check(OLD_LINE_TYPES)})) NOT VALID"
    )
    op.alter_column(
        "settlement_line",
        "line_type",
        type_=sa.String(20),
        existing_type=sa.String(24),
        existing_nullable=False,
    )


# ---------------------------------------------------------------------------
# Trigger function bodies. The M4 version adds the new content-bearing columns
# (settlement_run.economic_plan_id, settlement_line.addressee_owner_profile_id)
# to the "frozen once finalized" guards. The line trigger keeps the same
# freeze-via-parent logic (any UPDATE/DELETE on a finalized run's line forbidden)
# -- the new addressee column is just one more frozen column, already covered by
# the blanket "no UPDATE on a finalized run's line" rule, so the line trigger
# body is unchanged structurally; we re-state it for completeness/idempotence.
# ---------------------------------------------------------------------------
_SETTLEMENT_RUN_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION trg_settlement_run_immutability()
RETURNS TRIGGER AS $$
-- GoBD/Snapshot immutability for settlement_run (ADR-0003), M4 update.
-- Adds economic_plan_id to the frozen content columns. Allowed change on a
-- finalized run is still ONLY superseded_by_id (+ updated_at).
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'finalized' THEN
            RAISE EXCEPTION 'GoBD: DELETE of finalized settlement_run is forbidden (id=%)', OLD.id;
        END IF;
        RETURN OLD;
    END IF;

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
        IF NEW.economic_plan_id IS DISTINCT FROM OLD.economic_plan_id THEN
            RAISE EXCEPTION 'GoBD: Cannot change economic_plan_id on finalized settlement_run (id=%)', OLD.id;
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
        -- superseded_by_id + updated_at: the allowed changes.
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_SETTLEMENT_LINE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION trg_settlement_line_immutability()
RETURNS TRIGGER AS $$
-- Snapshot immutability for settlement_line (ADR-0003). Freeze condition: the
-- PARENT settlement_run is finalized. A finalized run's lines have no allowed
-- mutation (correction = new run); the new addressee_owner_profile_id column is
-- covered by the blanket rule. Unchanged from M3 structurally.
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
"""

# The M3 trigger bodies (for downgrade) -- WITHOUT the economic_plan_id guard.
_SETTLEMENT_RUN_TRIGGER_FN_M3 = """
CREATE OR REPLACE FUNCTION trg_settlement_run_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'finalized' THEN
            RAISE EXCEPTION 'GoBD: DELETE of finalized settlement_run is forbidden (id=%)', OLD.id;
        END IF;
        RETURN OLD;
    END IF;
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
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_SETTLEMENT_LINE_TRIGGER_FN_M3 = _SETTLEMENT_LINE_TRIGGER_FN
