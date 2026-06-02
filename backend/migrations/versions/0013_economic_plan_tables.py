"""M4: economic_plan + economic_plan_line tables (Wirtschaftsplan).

m4-weg-abrechnung.md section 1.3/1.4, section 10.2 (decision E4-2).

Own entities (SDD section 11/13) -- forward-looking plan, no Ist costs, no
Soll-Ist -- but with the SAME snapshot/hash/correction/RLS/trigger mechanic as
settlement_run (reused, not reinvented).

Grants (E3-5 line, like settlement_*): BOTH tables get FULL CRUD for app_rw --
editable `draft` state (recompute deletes/re-writes lines); the ADR-0003
immutability is enforced purely by the status-aware trigger (migration 0015).

Also adds the deferred FK settlement_run.economic_plan_id -> economic_plan.id
(the column was added in 0012; the FK is added here now that the table exists).

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_STATUSES = ("draft", "finalized")
PLAN_LINE_TYPES = (
    "PLANNED_COST",
    "RESERVE_CONTRIBUTION",
    "OWNER_ADVANCE",
    "SUBTOTAL",
)
DISTRIBUTION_KEYS = (
    "MEA", "WOHNFLAECHE", "BEHEIZTE_FLAECHE", "EINHEIT",
    "PERSONENZAHL", "VERBRAUCH", "EXTERNE_ABRECHNUNG", "MANUELLER_BETRAG",
)


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ======================================================================
    # economic_plan (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "economic_plan",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True),
                  sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("management_mandate_id", UUID(as_uuid=True),
                  sa.ForeignKey("management_mandate.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("plan_year", sa.SmallInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("monthly_due_day", sa.SmallInteger(), nullable=True),
        sa.Column("reserve_contribution_total", sa.Numeric(14, 2), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("snapshot_payload", JSONB, nullable=True),
        sa.Column("snapshot_sha256", sa.String(64), nullable=True),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("superseded_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("economic_plan.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("period_end >= period_start", name="ck_economic_plan_period"),
        sa.CheckConstraint(f"status IN ({_in_check(PLAN_STATUSES)})", name="ck_economic_plan_status"),
    )
    op.create_index("ix_economic_plan_organization_id", "economic_plan", ["organization_id"])
    op.create_index("ix_economic_plan_property_id", "economic_plan", ["property_id"])
    op.create_index("ix_economic_plan_property_year", "economic_plan",
                    ["property_id", "plan_year"])
    op.create_index("ix_economic_plan_superseded_by_id", "economic_plan", ["superseded_by_id"])

    # ======================================================================
    # economic_plan_line (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "economic_plan_line",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("economic_plan_id", UUID(as_uuid=True),
                  sa.ForeignKey("economic_plan.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("line_type", sa.String(24), nullable=False),
        sa.Column("cost_category_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_category.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("cost_category_code", sa.String(50), nullable=True),
        sa.Column("owner_profile_id", UUID(as_uuid=True),
                  sa.ForeignKey("owner_profile.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("unit_id", UUID(as_uuid=True),
                  sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("distribution_key", sa.String(20), nullable=True),
        sa.Column("distribution_base_total", sa.Numeric(12, 6), nullable=True),
        sa.Column("distribution_base_share", sa.Numeric(12, 6), nullable=True),
        sa.Column("planned_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("monthly_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("cost_group", sa.String(20), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"line_type IN ({_in_check(PLAN_LINE_TYPES)})",
                           name="ck_economic_plan_line_line_type"),
        sa.CheckConstraint(
            f"distribution_key IS NULL OR distribution_key IN ({_in_check(DISTRIBUTION_KEYS)})",
            name="ck_economic_plan_line_dist_key",
        ),
    )
    op.create_index("ix_economic_plan_line_organization_id", "economic_plan_line", ["organization_id"])
    op.create_index("ix_economic_plan_line_plan_id", "economic_plan_line", ["economic_plan_id"])
    op.create_index("ix_economic_plan_line_plan_sort", "economic_plan_line",
                    ["economic_plan_id", "sort_order"])

    # ======================================================================
    # Grants -- BOTH full CRUD (editable draft -> trigger-only freeze; E3-5)
    # ======================================================================
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON economic_plan TO app_rw")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON economic_plan_line TO app_rw")

    # ======================================================================
    # Deferred FK: settlement_run.economic_plan_id -> economic_plan.id
    # (column added in 0012; FK added now that economic_plan exists). RESTRICT:
    # a plan a finalized settlement references must not be deleted (GoBD chain).
    # ======================================================================
    op.create_foreign_key(
        "fk_settlement_run_economic_plan",
        "settlement_run",
        "economic_plan",
        ["economic_plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_settlement_run_economic_plan_id", "settlement_run", ["economic_plan_id"])


def downgrade() -> None:
    op.drop_index("ix_settlement_run_economic_plan_id", table_name="settlement_run")
    op.drop_constraint("fk_settlement_run_economic_plan", "settlement_run", type_="foreignkey")
    op.drop_table("economic_plan_line")
    op.drop_table("economic_plan")
