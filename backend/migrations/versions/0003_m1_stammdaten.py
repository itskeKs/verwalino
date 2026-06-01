"""M1 Stammdaten: property, unit, person, profiles, temporal tables, model anchors.

Adds btree_gist extension for EXCLUDE constraints on temporal tables.
EXCLUDE constraints enforce chronological integrity (I3):
  - ownership_history: no overlapping periods for the SAME (unit, owner_profile).
  - lease_agreement: no overlapping lease periods PER UNIT.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable btree_gist extension for EXCLUDE constraints with daterange
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # --- property ---
    op.create_table(
        "property",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("street", sa.String(255), nullable=False, server_default=""),
        sa.Column("zip_code", sa.String(10), nullable=False, server_default=""),
        sa.Column("city", sa.String(255), nullable=False, server_default=""),
        sa.Column("management_type", sa.String(5), nullable=False),
        sa.Column("fiscal_year_start_month", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("management_type IN ('MIET', 'WEG', 'SEV', 'MISCH')", name="ck_property_management_type"),
        sa.CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="ck_property_fiscal_month"),
    )
    op.create_index("ix_property_organization_id", "property", ["organization_id"])

    # --- unit ---
    op.create_table(
        "unit",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True), sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("area", sa.Numeric(10, 2), nullable=True),
        sa.Column("heated_area", sa.Numeric(10, 2), nullable=True),
        sa.Column("usage_type", sa.String(12), nullable=False),
        sa.Column("mea", sa.Numeric(12, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("usage_type IN ('WOHNUNG', 'GEWERBE', 'STELLPLATZ', 'SONSTIGE')", name="ck_unit_usage_type"),
    )
    op.create_index("ix_unit_organization_id", "unit", ["organization_id"])
    op.create_index("ix_unit_property_id", "unit", ["property_id"])

    # --- person ---
    op.create_table(
        "person",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("first_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(255), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("street", sa.String(255), nullable=False, server_default=""),
        sa.Column("zip_code", sa.String(10), nullable=False, server_default=""),
        sa.Column("city", sa.String(255), nullable=False, server_default=""),
        sa.Column("iban", sa.String(34), nullable=True),
        sa.Column("bic", sa.String(11), nullable=True),
        sa.Column("bank_name", sa.String(255), nullable=True),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_restricted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_person_organization_id", "person", ["organization_id"])

    # --- owner_profile ---
    op.create_table(
        "owner_profile",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("person.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_owner_profile_organization_id", "owner_profile", ["organization_id"])
    op.create_index("ix_owner_profile_person_id", "owner_profile", ["person_id"])

    # --- tenant_profile ---
    op.create_table(
        "tenant_profile",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("person.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_tenant_profile_organization_id", "tenant_profile", ["organization_id"])
    op.create_index("ix_tenant_profile_person_id", "tenant_profile", ["person_id"])

    # --- vendor ---
    op.create_table(
        "vendor",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("person.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tax_id", sa.String(20), nullable=True),
        sa.Column("vat_id", sa.String(20), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_vendor_organization_id", "vendor", ["organization_id"])
    op.create_index("ix_vendor_person_id", "vendor", ["person_id"])

    # --- ownership_history (temporal, EXCLUDE constraint) ---
    op.create_table(
        "ownership_history",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_id", UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_profile_id", UUID(as_uuid=True), sa.ForeignKey("owner_profile.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("mea_share", sa.Numeric(12, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_ownership_history_dates"),
    )
    op.create_index("ix_ownership_history_organization_id", "ownership_history", ["organization_id"])
    op.create_index("ix_ownership_history_unit_id", "ownership_history", ["unit_id"])
    op.create_index("ix_ownership_history_owner_profile_id", "ownership_history", ["owner_profile_id"])

    # EXCLUDE constraint: no overlapping periods for the SAME (unit_id, owner_profile_id).
    # Uses daterange with '[)' (closed start, open end).
    # COALESCE handles NULL valid_to as 'infinity' (open-ended ownership).
    # Multiple co-owners (different owner_profiles) for the same unit at the same time ARE allowed.
    op.execute("""
        ALTER TABLE ownership_history
        ADD CONSTRAINT excl_ownership_no_overlap
        EXCLUDE USING gist (
            unit_id WITH =,
            owner_profile_id WITH =,
            daterange(valid_from, valid_to, '[)') WITH &&
        )
    """)

    # --- lease_agreement (temporal, EXCLUDE constraint) ---
    op.create_table(
        "lease_agreement",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_id", UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tenant_profile_id", UUID(as_uuid=True), sa.ForeignKey("tenant_profile.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("base_rent", sa.Numeric(14, 2), nullable=False),
        sa.Column("advance_payment", sa.Numeric(14, 2), nullable=False),
        sa.Column("deposit", sa.Numeric(14, 2), nullable=True),
        sa.Column("notes", sa.String(2000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_lease_agreement_dates"),
    )
    op.create_index("ix_lease_agreement_organization_id", "lease_agreement", ["organization_id"])
    op.create_index("ix_lease_agreement_unit_id", "lease_agreement", ["unit_id"])
    op.create_index("ix_lease_agreement_tenant_profile_id", "lease_agreement", ["tenant_profile_id"])

    # EXCLUDE constraint: no overlapping lease periods PER UNIT.
    # One unit can have at most one active lease at any point in time.
    # Gaps (vacancy) are allowed.
    op.execute("""
        ALTER TABLE lease_agreement
        ADD CONSTRAINT excl_lease_no_overlap
        EXCLUDE USING gist (
            unit_id WITH =,
            daterange(valid_from, valid_to, '[)') WITH &&
        )
    """)

    # --- bank_account (model anchor for M6) ---
    op.create_table(
        "bank_account",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True), sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_type", sa.String(10), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default=""),
        sa.Column("iban", sa.String(34), nullable=True),
        sa.Column("bic", sa.String(11), nullable=True),
        sa.Column("bank_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("account_type IN ('LAUFEND', 'RUECKLAGE', 'KAUTION')", name="ck_bank_account_type"),
    )
    op.create_index("ix_bank_account_organization_id", "bank_account", ["organization_id"])
    op.create_index("ix_bank_account_property_id", "bank_account", ["property_id"])

    # --- meter ---
    op.create_table(
        "meter",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True), sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_id", UUID(as_uuid=True), sa.ForeignKey("unit.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("meter_number", sa.String(100), nullable=False),
        sa.Column("meter_type", sa.String(50), nullable=False),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_meter_organization_id", "meter", ["organization_id"])
    op.create_index("ix_meter_property_id", "meter", ["property_id"])

    # --- meter_reading ---
    op.create_table(
        "meter_reading",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("meter_id", UUID(as_uuid=True), sa.ForeignKey("meter.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reading_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(14, 4), nullable=False),
        sa.Column("period", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_meter_reading_organization_id", "meter_reading", ["organization_id"])
    op.create_index("ix_meter_reading_meter_id", "meter_reading", ["meter_id"])

    # --- Grant privileges to app_rw on all new tables ---
    new_tables = [
        "property", "unit", "person", "owner_profile", "tenant_profile",
        "vendor", "ownership_history", "lease_agreement", "bank_account",
        "meter", "meter_reading",
    ]
    for table in new_tables:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_rw")


def downgrade() -> None:
    tables = [
        "meter_reading", "meter", "bank_account",
        "lease_agreement", "ownership_history",
        "vendor", "tenant_profile", "owner_profile",
        "person", "unit", "property",
    ]
    for table in tables:
        op.drop_table(table)

    # Note: btree_gist extension is left in place (shared, idempotent)
