"""M3: settlement_run + settlement_line tables.

m3-abrechnungs-engine.md section 1.4/1.5, section 10.2.

settlement_run: tenant-behaftet; run_type discriminator (BK_MIETER in M3,
WEG/Wirtschaftsplan/Vermoegensbericht reserved for M4); snapshot_payload JSONB
+ snapshot_sha256; status draft|finalized; superseded_by_id forward pointer (E-4).

settlement_line: tenant-behaftet; carries all per-position BGH-Pflichtangaben.

Grants (decision E3-5, section 1.6): BOTH tables get FULL CRUD for app_rw --
they have an editable `draft` state (recompute deletes/re-writes lines), so the
ADR-0003 immutability is enforced purely by the status-aware trigger (migration
0011), NOT by withholding the DELETE grant (unlike invoice_document).

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Controlled vocabularies (String + CHECK, no DB enums).
RUN_TYPES = (
    "BK_MIETER",            # M3
    "WEG_JAHRESABRECHNUNG", # M4 (reserved)
    "WIRTSCHAFTSPLAN",      # M4 (reserved)
    "VERMOEGENSBERICHT",    # M4 (reserved)
)
RUN_STATUSES = ("draft", "finalized")
ADVANCE_SOURCES = ("MANUAL", "DERIVED")
LINE_TYPES = ("COST", "ADVANCE_PAYMENT", "BALANCE", "SUBTOTAL")
# Same key vocabulary as M2 cost_entry (section 8).
DISTRIBUTION_KEYS = (
    "MEA", "WOHNFLAECHE", "BEHEIZTE_FLAECHE", "EINHEIT",
    "PERSONENZAHL", "VERBRAUCH", "EXTERNE_ABRECHNUNG", "MANUELLER_BETRAG",
)


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ======================================================================
    # settlement_run (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "settlement_run",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True),
                  sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("management_mandate_id", UUID(as_uuid=True),
                  sa.ForeignKey("management_mandate.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("run_type", sa.String(24), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("unit_id", UUID(as_uuid=True),
                  sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("lease_agreement_id", UUID(as_uuid=True),
                  sa.ForeignKey("lease_agreement.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("tenant_profile_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenant_profile.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("advance_payments_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("advance_payments_source", sa.String(12), nullable=False,
                  server_default=sa.text("'MANUAL'")),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("snapshot_payload", JSONB, nullable=True),
        sa.Column("snapshot_sha256", sa.String(64), nullable=True),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("superseded_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("settlement_run.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("period_end >= period_start", name="ck_settlement_run_period"),
        sa.CheckConstraint(f"run_type IN ({_in_check(RUN_TYPES)})", name="ck_settlement_run_run_type"),
        sa.CheckConstraint(f"status IN ({_in_check(RUN_STATUSES)})", name="ck_settlement_run_status"),
        sa.CheckConstraint(
            f"advance_payments_source IN ({_in_check(ADVANCE_SOURCES)})",
            name="ck_settlement_run_advance_source",
        ),
    )
    op.create_index("ix_settlement_run_organization_id", "settlement_run", ["organization_id"])
    op.create_index("ix_settlement_run_property_id", "settlement_run", ["property_id"])
    op.create_index("ix_settlement_run_lease_agreement_id", "settlement_run", ["lease_agreement_id"])
    op.create_index("ix_settlement_run_org_type_status", "settlement_run",
                    ["organization_id", "run_type", "status"])
    op.create_index("ix_settlement_run_property_period", "settlement_run",
                    ["property_id", "period_start", "period_end"])
    op.create_index("ix_settlement_run_superseded_by_id", "settlement_run", ["superseded_by_id"])

    # ======================================================================
    # settlement_line (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "settlement_line",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("settlement_run_id", UUID(as_uuid=True),
                  sa.ForeignKey("settlement_run.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("line_type", sa.String(20), nullable=False),
        sa.Column("cost_category_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_category.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("cost_category_code", sa.String(50), nullable=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("distribution_key", sa.String(20), nullable=True),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("distribution_base_total", sa.Numeric(12, 6), nullable=True),
        sa.Column("distribution_base_share", sa.Numeric(12, 6), nullable=True),
        sa.Column("time_share_days", sa.SmallInteger(), nullable=True),
        sa.Column("time_share_total_days", sa.SmallInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"line_type IN ({_in_check(LINE_TYPES)})", name="ck_settlement_line_line_type"),
        sa.CheckConstraint(
            f"distribution_key IS NULL OR distribution_key IN ({_in_check(DISTRIBUTION_KEYS)})",
            name="ck_settlement_line_dist_key",
        ),
    )
    op.create_index("ix_settlement_line_organization_id", "settlement_line", ["organization_id"])
    op.create_index("ix_settlement_line_settlement_run_id", "settlement_line", ["settlement_run_id"])
    op.create_index("ix_settlement_line_run_sort", "settlement_line",
                    ["settlement_run_id", "sort_order"])

    # ======================================================================
    # Grants -- BOTH full CRUD (editable draft -> trigger-only freeze; E3-5)
    # ======================================================================
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON settlement_run TO app_rw")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON settlement_line TO app_rw")


def downgrade() -> None:
    op.drop_table("settlement_line")
    op.drop_table("settlement_run")
