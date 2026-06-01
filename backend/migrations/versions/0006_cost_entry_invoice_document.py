"""M2: cost_entry + invoice_document tables.

cost_entry: tenant-behaftet, all CHECK constraints for status/distribution/apportionability.
invoice_document: tenant-behaftet, chain_root_id + partial unique index for "exactly one active per chain".

Grants:
  cost_entry: full CRUD for app_rw (drafts are deletable; trigger guards confirmed rows).
  invoice_document: SELECT, INSERT, UPDATE for app_rw -- NO DELETE (ADR-0003 backstop, decision E-3).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Controlled vocabularies (same as 0005, repeated here for self-containment)
COST_ENTRY_STATUSES = ("draft_extracted", "needs_review", "confirmed", "rejected")
SOURCES = ("MANUAL", "PDF_IMPORT", "E_INVOICE")
DISTRIBUTION_KEYS = (
    "MEA", "WOHNFLAECHE", "BEHEIZTE_FLAECHE", "EINHEIT",
    "PERSONENZAHL", "VERBRAUCH", "EXTERNE_ABRECHNUNG", "MANUELLER_BETRAG",
)
TENANT_APPORTIONABILITY = (
    "UMLAGEFAEHIG", "UMLAGEFAEHIG_EXTERN_PRUEFEN", "GEMISCHT_PRUEFEN",
    "TEILWEISE_PRUEFEN", "MANUELL_PRUEFEN", "NICHT_UMLAGEFAEHIG",
)
DOC_STATUSES = ("active", "superseded")
STORAGE_BACKENDS = ("LOCAL_FS", "S3")


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ======================================================================
    # cost_entry (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "cost_entry",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True),
                  sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("management_mandate_id", UUID(as_uuid=True),
                  sa.ForeignKey("management_mandate.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("cost_category_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_category.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cost_category_plan_version", sa.SmallInteger(), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True),
                  sa.ForeignKey("vendor.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("service_period_from", sa.Date(), nullable=False),
        sa.Column("service_period_to", sa.Date(), nullable=False),
        sa.Column("gross_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("vat_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("document_number", sa.String(100), nullable=True),
        sa.Column("owner_distribution_key", sa.String(20), nullable=True),
        sa.Column("tenant_distribution_key", sa.String(20), nullable=True),
        sa.Column("tenant_apportionable", sa.String(30), nullable=False),
        sa.Column("apportionable_tenant_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("superseded_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_entry.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # CHECK constraints (String+CHECK, no DB enums)
        sa.CheckConstraint(
            "service_period_to >= service_period_from",
            name="ck_cost_entry_service_period",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_check(COST_ENTRY_STATUSES)})",
            name="ck_cost_entry_status",
        ),
        sa.CheckConstraint(
            f"source IN ({_in_check(SOURCES)})",
            name="ck_cost_entry_source",
        ),
        sa.CheckConstraint(
            f"owner_distribution_key IS NULL OR "
            f"owner_distribution_key IN ({_in_check(DISTRIBUTION_KEYS)})",
            name="ck_cost_entry_owner_dist_key",
        ),
        sa.CheckConstraint(
            f"tenant_distribution_key IS NULL OR "
            f"tenant_distribution_key IN ({_in_check(DISTRIBUTION_KEYS)})",
            name="ck_cost_entry_tenant_dist_key",
        ),
        sa.CheckConstraint(
            f"tenant_apportionable IN ({_in_check(TENANT_APPORTIONABILITY)})",
            name="ck_cost_entry_tenant_apport",
        ),
    )

    # Indices (section 2.2)
    op.create_index("ix_cost_entry_organization_id", "cost_entry", ["organization_id"])
    op.create_index("ix_cost_entry_property_id", "cost_entry", ["property_id"])
    op.create_index("ix_cost_entry_cost_category_id", "cost_entry", ["cost_category_id"])
    op.create_index("ix_cost_entry_vendor_id", "cost_entry", ["vendor_id"])
    op.create_index("ix_cost_entry_org_status", "cost_entry", ["organization_id", "status"])
    op.create_index("ix_cost_entry_property_service_period", "cost_entry",
                    ["property_id", "service_period_from", "service_period_to"])

    # ======================================================================
    # invoice_document (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "invoice_document",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cost_entry_id", UUID(as_uuid=True),
                  sa.ForeignKey("cost_entry.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("storage_backend", sa.String(16), nullable=False),
        sa.Column("storage_version", sa.String(128), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("content_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("supersedes_id", UUID(as_uuid=True),
                  sa.ForeignKey("invoice_document.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("chain_root_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'active'")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_payload", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # CHECK constraints
        sa.CheckConstraint(
            f"status IN ({_in_check(DOC_STATUSES)})",
            name="ck_invoice_document_status",
        ),
        sa.CheckConstraint(
            f"storage_backend IN ({_in_check(STORAGE_BACKENDS)})",
            name="ck_invoice_document_storage_backend",
        ),
    )

    # Indices (section 4.2)
    op.create_index("ix_invoice_document_organization_id", "invoice_document", ["organization_id"])
    op.create_index("ix_invoice_document_cost_entry_id", "invoice_document", ["cost_entry_id"])
    op.create_index("ix_invoice_document_org_status", "invoice_document", ["organization_id", "status"])
    op.create_index("ix_invoice_document_content_sha256", "invoice_document", ["content_sha256"])
    op.create_index("ix_invoice_document_supersedes_id", "invoice_document", ["supersedes_id"])
    op.create_index("ix_invoice_document_retention_until", "invoice_document", ["retention_until"])

    # Partial unique index: exactly one active document per chain (section 4.3, decision E-5)
    op.execute("""
        CREATE UNIQUE INDEX uq_active_doc_per_chain
        ON invoice_document (chain_root_id)
        WHERE status = 'active'
    """)

    # ======================================================================
    # Grants
    # ======================================================================
    # cost_entry: full CRUD (drafts deletable; trigger guards confirmed rows)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cost_entry TO app_rw")

    # invoice_document: NO DELETE (ADR-0003, decision E-3 confirmed)
    op.execute("GRANT SELECT, INSERT, UPDATE ON invoice_document TO app_rw")


def downgrade() -> None:
    op.drop_table("invoice_document")
    op.drop_table("cost_entry")
