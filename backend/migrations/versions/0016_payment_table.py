"""M6: payment table + Vermoegens-Kohaerenz Composite-FK.

m6-zahlungen-mietkonto.md section 1.2/1.4/4/8; ADR-0009 (booking record),
ADR-0010 (Composite-FK kohaerenz).

payment: tenant-behaftet; a captured money movement (IN/OUT) against exactly one
bank_account, with a controlled payment_type vocabulary (the abrechnungs steering
key, section 1.3), an explicit Periodenbezug (period_year/_month), the
draft|booked status (immutable once booked, trigger in 0018) and the Storno chain
(reverses_id / reversed_by_id forward pointer, E-4).

Vermoegens-Kohaerenz (ADR-0010): the Composite-FK
  payment(organization_id, bank_account_id, property_id)
  -> bank_account(organization_id, id, property_id)
makes "payment on account X claims property Y though X belongs to Z" DB-impossible.
It REQUIRES a UNIQUE index on bank_account(organization_id, id, property_id) --
added here additively (bank_account is otherwise structurally UNCHANGED, E6-5:
no Saldenspalte; the Kontostand stays derived from payments).

Grants (decision E3-5, like settlement_run / cost_entry): FULL CRUD for app_rw --
payment has an editable `draft` state, so the ADR-0003 immutability is enforced
purely by the status-aware trigger (migration 0018), NOT by withholding DELETE.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Controlled vocabularies (String + CHECK, no DB enums) -- m6 section 1.2/1.3.
DIRECTIONS = ("IN", "OUT")
PAYMENT_STATUSES = ("draft", "booked")
# [F6-3] provisional vocabulary; KAUTION optional/abrechnungsneutral. Fachlich
# abzunehmen (m6 section 1.3). The type STEERS the abrechnungs-anbindung (§6).
PAYMENT_TYPES = (
    "KALTMIETE",
    "NK_VORAUSZAHLUNG",
    "HAUSGELD_VORSCHUSS",
    "RUECKLAGENANTEIL",
    "SONDERZAHLUNG",
    "NACHZAHLUNG",
    "KAUTION",
)
# Tenant-bound vs owner-bound types (gegenpartei-/typ-konsistenz, m6 section 1.4).
TENANT_TYPES = ("KALTMIETE", "NK_VORAUSZAHLUNG", "KAUTION")
OWNER_TYPES = ("HAUSGELD_VORSCHUSS", "RUECKLAGENANTEIL", "SONDERZAHLUNG")
# NACHZAHLUNG: either counterparty, but EXACTLY one must be set.


def _in_check(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ======================================================================
    # Prerequisite for the Composite-FK (ADR-0010): a UNIQUE index on
    # bank_account(organization_id, id, property_id). `id` is already the PK
    # (unique alone), so this triple is trivially unique -- it exists ONLY to
    # be a referenceable target for payment's Composite-FK. bank_account stays
    # otherwise unchanged (E6-5: no Saldenspalte).
    # ======================================================================
    op.create_unique_constraint(
        "uq_bank_account_org_id_property",
        "bank_account",
        ["organization_id", "id", "property_id"],
    )

    # ======================================================================
    # payment (tenant-behaftet)
    # ======================================================================
    op.create_table(
        "payment",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bank_account_id", UUID(as_uuid=True),
                  sa.ForeignKey("bank_account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True),
                  sa.ForeignKey("property.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("management_mandate_id", UUID(as_uuid=True),
                  sa.ForeignKey("management_mandate.id", ondelete="RESTRICT"), nullable=True),
        # Money movement.
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        # Counterparty.
        sa.Column("tenant_profile_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenant_profile.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("owner_profile_id", UUID(as_uuid=True),
                  sa.ForeignKey("owner_profile.id", ondelete="RESTRICT"), nullable=True),
        # Zweck/Typ + Zuordnung.
        sa.Column("payment_type", sa.String(24), nullable=False),
        sa.Column("lease_agreement_id", UUID(as_uuid=True),
                  sa.ForeignKey("lease_agreement.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("period_year", sa.SmallInteger(), nullable=True),
        sa.Column("period_month", sa.SmallInteger(), nullable=True),
        sa.Column("settlement_run_id", UUID(as_uuid=True),
                  sa.ForeignKey("settlement_run.id", ondelete="RESTRICT"), nullable=True),
        # GoBD / immutability.
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booked_by", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        # Storno chain (self-FK).
        sa.Column("reverses_id", UUID(as_uuid=True),
                  sa.ForeignKey("payment.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reversed_by_id", UUID(as_uuid=True),
                  sa.ForeignKey("payment.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # --- CHECK constraints (m6 section 1.2/1.4) ---
        sa.CheckConstraint(f"direction IN ({_in_check(DIRECTIONS)})", name="ck_payment_direction"),
        sa.CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        sa.CheckConstraint(f"status IN ({_in_check(PAYMENT_STATUSES)})", name="ck_payment_status"),
        sa.CheckConstraint(f"payment_type IN ({_in_check(PAYMENT_TYPES)})", name="ck_payment_type"),
        sa.CheckConstraint(
            "period_month IS NULL OR period_month BETWEEN 1 AND 12",
            name="ck_payment_period_month",
        ),
        # Gegenpartei-/Typ-Konsistenz (section 1.4):
        #   tenant types  -> tenant set, owner NULL.
        #   owner types   -> owner set, tenant NULL.
        #   NACHZAHLUNG   -> exactly one of the two set.
        sa.CheckConstraint(
            f"""
            CASE
              WHEN payment_type IN ({_in_check(TENANT_TYPES)})
                THEN tenant_profile_id IS NOT NULL AND owner_profile_id IS NULL
              WHEN payment_type IN ({_in_check(OWNER_TYPES)})
                THEN owner_profile_id IS NOT NULL AND tenant_profile_id IS NULL
              WHEN payment_type = 'NACHZAHLUNG'
                THEN (tenant_profile_id IS NOT NULL) <> (owner_profile_id IS NOT NULL)
              ELSE TRUE
            END
            """,
            name="ck_payment_counterparty_type",
        ),
        # --- Vermoegens-Kohaerenz Composite-FK (ADR-0010) ---
        sa.ForeignKeyConstraint(
            ["organization_id", "bank_account_id", "property_id"],
            ["bank_account.organization_id", "bank_account.id", "bank_account.property_id"],
            name="fk_payment_bank_account_coherence",
            ondelete="RESTRICT",
        ),
    )

    # --- Indices (m6 section 1.2) ---
    op.create_index("ix_payment_organization_id", "payment", ["organization_id"])
    op.create_index("ix_payment_bank_account_id", "payment", ["bank_account_id"])
    op.create_index("ix_payment_property_year", "payment", ["property_id", "period_year"])
    op.create_index("ix_payment_lease_agreement_id", "payment", ["lease_agreement_id"])
    op.create_index("ix_payment_tenant_year", "payment", ["tenant_profile_id", "period_year"])
    op.create_index("ix_payment_owner_year", "payment", ["owner_profile_id", "period_year"])
    op.create_index("ix_payment_payment_type", "payment", ["payment_type"])
    op.create_index("ix_payment_settlement_run_id", "payment", ["settlement_run_id"])
    op.create_index("ix_payment_reverses_id", "payment", ["reverses_id"])
    op.create_index("ix_payment_reversed_by_id", "payment", ["reversed_by_id"])

    # --- Grants (E3-5: full CRUD; trigger-only freeze in 0018) ---
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON payment TO app_rw")


def downgrade() -> None:
    op.drop_table("payment")
    op.drop_constraint("uq_bank_account_org_id_property", "bank_account", type_="unique")
