"""Core tables: organization, user, membership, management_mandate, audit_event.

Revision ID: 0001
Revises: None
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- organization (global, no org_id) ---
    op.create_table(
        "organization",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # --- user (tenant-behaftet) ---
    op.create_table(
        "user",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("organization_id", "email", name="uq_user_org_email"),
    )
    op.create_index("ix_user_organization_id", "user", ["organization_id"])

    # --- membership (tenant-behaftet) ---
    op.create_table(
        "membership",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
    )
    op.create_index("ix_membership_organization_id", "membership", ["organization_id"])

    # --- management_mandate (tenant-behaftet) ---
    op.create_table(
        "management_mandate",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mandate_type", sa.String(4), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default=""),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_mandate_dates"),
        sa.CheckConstraint("mandate_type IN ('WEG', 'MIET', 'SEV')", name="ck_mandate_type"),
    )
    op.create_index("ix_management_mandate_organization_id", "management_mandate", ["organization_id"])

    # --- audit_event (tenant-behaftet, append-only) ---
    op.create_table(
        "audit_event",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload_diff", JSONB, nullable=True),
        sa.Column("prev_event_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_event_organization_id", "audit_event", ["organization_id"])

    # --- Grant table-level privileges to app_rw ---
    # app_rw can SELECT, INSERT, UPDATE, DELETE on most tables.
    # audit_event: only SELECT and INSERT (append-only).
    for table in ("organization", '"user"', "membership", "management_mandate"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_rw")

    op.execute('GRANT SELECT, INSERT ON audit_event TO app_rw')
    # Explicitly: NO UPDATE, NO DELETE on audit_event for app_rw


def downgrade() -> None:
    op.drop_table("audit_event")
    op.drop_table("management_mandate")
    op.drop_table("membership")
    op.drop_table("user")
    op.drop_table("organization")
