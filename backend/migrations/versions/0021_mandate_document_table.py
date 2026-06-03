"""Mandate documents: mandate_document table (Verträge & Unterlagen am Mandat).

Stores documents attached to a management mandate -- Verwaltervertrag, Bankkonto-
Unterlagen, Vollmachten, Versicherungen etc. These are contract/admin documents,
NOT GoBD Buchungsbelege, so (unlike invoice_document) they are mutable/deletable:
full CRUD grant, no immutability trigger. Tenant-isolated via RLS (same fail-
closed pattern as 0002/0007/0020). File bytes live in the content-addressed
document store; this table holds the metadata + storage reference.

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mandate_document"

_DOC_TYPES = ("VERWALTERVERTRAG", "BANKKONTO", "VOLLMACHT", "VERSICHERUNG", "SONSTIGES")


def _add_rls(table_name: str) -> None:
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    for cmd_suffix, clause in [
        ("select",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("insert",
         "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("update",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid) "
         "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
        ("delete",
         "USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"),
    ]:
        op.execute(
            f'CREATE POLICY org_isolation_{cmd_suffix} ON "{table_name}" '
            f'FOR {cmd_suffix.upper()} {clause}'
        )


def upgrade() -> None:
    doc_type_in = ", ".join(f"'{t}'" for t in _DOC_TYPES)
    op.create_table(
        _TABLE,
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("management_mandate_id", UUID(as_uuid=True),
                  sa.ForeignKey("management_mandate.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("doc_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("content_byte_size", sa.BigInteger, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("storage_backend", sa.String(16), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"doc_type IN ({doc_type_in})", name="ck_mandate_document_type"),
    )
    _add_rls(_TABLE)
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{_TABLE}" TO app_rw')


def downgrade() -> None:
    for suffix in ("select", "insert", "update", "delete"):
        op.execute(f'DROP POLICY IF EXISTS org_isolation_{suffix} ON "{_TABLE}"')
    op.execute(f'ALTER TABLE "{_TABLE}" DISABLE ROW LEVEL SECURITY')
    op.drop_table(_TABLE)
