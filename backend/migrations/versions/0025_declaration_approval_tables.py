"""Approved declaration facts, rule graph, provenance, and least privilege.

Revision ID: 0025
Revises: 0024
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


APPROVAL_TABLES = (
    "property_legal_reference", "property_assignment", "property_document_relation",
    "property_rule", "property_rule_relation", "property_rule_source", "approved_fact_source",
)


def _id_org_timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def _add_rls(table: str) -> None:
    quoted = f'"{table}"'
    op.execute(f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY")
    expression = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
    op.execute(f"CREATE POLICY {table}_tenant_select ON {quoted} FOR SELECT USING ({expression})")
    op.execute(f"CREATE POLICY {table}_tenant_insert ON {quoted} FOR INSERT WITH CHECK ({expression})")
    op.execute(f"CREATE POLICY {table}_tenant_update ON {quoted} FOR UPDATE USING ({expression}) WITH CHECK ({expression})")
    op.execute(f"CREATE POLICY {table}_tenant_delete ON {quoted} FOR DELETE USING ({expression})")


def _indexes(table: str, definitions: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
    for name, columns in definitions:
        op.create_index(name, table, list(columns))


def _grants() -> None:
    names = ", ".join(f'"{table}"' for table in APPROVAL_TABLES)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {names} FROM app_rw")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {names} TO app_rw")
    op.execute('GRANT UPDATE (status, valid_to, updated_at) ON "property_legal_reference" TO app_rw')
    op.execute('GRANT UPDATE (status, valid_to, updated_at) ON "property_assignment" TO app_rw')
    op.execute('GRANT UPDATE (status, valid_to, search_text, updated_at) ON "property_rule" TO app_rw')
    op.execute('GRANT UPDATE (status, updated_at) ON "property_document_relation" TO app_rw')
    op.execute('GRANT UPDATE (status, valid_to, updated_at) ON "approved_fact_source" TO app_rw')


def _triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_approved_fact_source_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.property_id IS DISTINCT FROM OLD.property_id
               OR NEW.subject_kind IS DISTINCT FROM OLD.subject_kind
               OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
               OR NEW.field_key IS DISTINCT FROM OLD.field_key
               OR NEW.approved_value_json IS DISTINCT FROM OLD.approved_value_json
               OR NEW.value_fingerprint IS DISTINCT FROM OLD.value_fingerprint
               OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
               OR NEW.property_document_id IS DISTINCT FROM OLD.property_document_id
               OR NEW.pdf_page IS DISTINCT FROM OLD.pdf_page
               OR NEW.printed_page IS DISTINCT FROM OLD.printed_page
               OR NEW.section IS DISTINCT FROM OLD.section
               OR NEW.quote IS DISTINCT FROM OLD.quote
               OR NEW.source_import_id IS DISTINCT FROM OLD.source_import_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'approved_fact_source authoritative fields are immutable';
            END IF;
            IF NEW.status = OLD.status THEN
                IF NEW.valid_to IS DISTINCT FROM OLD.valid_to THEN
                    RAISE EXCEPTION 'approved_fact_source valid_to requires superseding lifecycle';
                END IF;
            ELSIF OLD.status <> 'EFFECTIVE' OR NEW.status <> 'SUPERSEDED' OR NEW.valid_to IS NULL THEN
                RAISE EXCEPTION 'approved_fact_source only permits EFFECTIVE to SUPERSEDED';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_approved_fact_source_update
        BEFORE UPDATE ON approved_fact_source
        FOR EACH ROW EXECUTE FUNCTION guard_approved_fact_source_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_property_rule_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.property_id IS DISTINCT FROM OLD.property_id
               OR NEW.stable_key IS DISTINCT FROM OLD.stable_key
               OR NEW.topic IS DISTINCT FROM OLD.topic
               OR NEW.title IS DISTINCT FROM OLD.title
               OR NEW.summary IS DISTINCT FROM OLD.summary
               OR NEW.scope_json IS DISTINCT FROM OLD.scope_json
               OR NEW.effect_json IS DISTINCT FROM OLD.effect_json
               OR NEW.source_import_id IS DISTINCT FROM OLD.source_import_id
               OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
               OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
               OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'property_rule canonical fields are immutable';
            END IF;
            IF char_length(NEW.search_text) > 100000 THEN
                RAISE EXCEPTION 'property_rule search_text exceeds 100000 characters';
            END IF;
            IF OLD.status = 'EFFECTIVE' AND NEW.status NOT IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION', 'RETRACTED') THEN
                RAISE EXCEPTION 'invalid property_rule lifecycle transition';
            ELSIF OLD.status = 'NEEDS_CLARIFICATION' AND NEW.status NOT IN ('NEEDS_CLARIFICATION', 'EFFECTIVE', 'SUPERSEDED', 'RETRACTED') THEN
                RAISE EXCEPTION 'invalid property_rule lifecycle transition';
            ELSIF OLD.status IN ('SUPERSEDED', 'RETRACTED') AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'closed property_rule cannot be reopened';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_property_rule_update
        BEFORE UPDATE ON property_rule
        FOR EACH ROW EXECUTE FUNCTION guard_property_rule_update()
        """
    )


def upgrade() -> None:
    # Composite identities prevent legal facts from pointing across either a tenant or property.
    op.create_unique_constraint("uq_unit_organization_property_id", "unit", ["organization_id", "property_id", "id"])
    op.create_unique_constraint("uq_property_document_organization_property_id", "property_document", ["organization_id", "property_id", "id"])

    op.create_table(
        "property_legal_reference", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False),
        sa.Column("reference_type", sa.String(48), nullable=False), sa.Column("district", sa.String(255)),
        sa.Column("cadastral_district", sa.String(255)), sa.Column("field_number", sa.String(100)),
        sa.Column("parcel", sa.String(100)), sa.Column("sheet", sa.String(100)),
        sa.Column("details_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="EFFECTIVE"),
        sa.Column("valid_from", sa.DateTime(timezone=True)), sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("source_import_id", UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_legal_reference_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_legal_reference_import_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION')", name=op.f("ck_property_legal_reference_status")),
        sa.CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name=op.f("ck_property_legal_reference_validity")),
    )
    _indexes("property_legal_reference", (("ix_property_legal_reference_org_property", ("organization_id", "property_id")), ("ix_property_legal_reference_source_import", ("source_import_id",))))

    op.create_table(
        "property_assignment", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False), sa.Column("unit_id", UUID(as_uuid=True)),
        sa.Column("stable_key", sa.String(255), nullable=False), sa.Column("assignment_type", sa.String(64), nullable=False),
        sa.Column("designation", sa.String(255), nullable=False), sa.Column("location", sa.String(255)), sa.Column("description", sa.String(4000)),
        sa.Column("details_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(24), nullable=False, server_default="EFFECTIVE"),
        sa.Column("valid_from", sa.DateTime(timezone=True)), sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("source_import_id", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint("organization_id", "property_id", "stable_key", name="uq_property_assignment_property_stable_key"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_assignment_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "unit_id"], ["unit.organization_id", "unit.property_id", "unit.id"], name="fk_property_assignment_unit_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_assignment_import_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION')", name=op.f("ck_property_assignment_status")),
        sa.CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name=op.f("ck_property_assignment_validity")),
    )
    _indexes("property_assignment", (("ix_property_assignment_org_property", ("organization_id", "property_id")), ("ix_property_assignment_unit_property", ("organization_id", "property_id", "unit_id")), ("ix_property_assignment_source_import", ("source_import_id",))))

    op.create_table(
        "property_document_relation", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False), sa.Column("source_document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_document_id", UUID(as_uuid=True), nullable=False), sa.Column("relation_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="EFFECTIVE"), sa.Column("source_import_id", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint("source_document_id", "target_document_id", "relation_type", name="uq_property_document_relation_edge"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_document_relation_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "source_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_property_document_relation_source_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "target_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_property_document_relation_target_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_document_relation_import_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("relation_type IN ('AMENDS', 'SUPPLEMENTS', 'REFERENCES')", name=op.f("ck_property_document_relation_type")),
        sa.CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION')", name=op.f("ck_property_document_relation_status")),
        sa.CheckConstraint("source_document_id <> target_document_id", name=op.f("ck_property_document_relation_distinct_documents")),
    )
    _indexes("property_document_relation", (("ix_property_document_relation_org_property", ("organization_id", "property_id")), ("ix_property_document_relation_source_document", ("organization_id", "property_id", "source_document_id")), ("ix_property_document_relation_target_document", ("organization_id", "property_id", "target_document_id")), ("ix_property_document_relation_source_import", ("source_import_id",))))

    op.create_table(
        "property_rule", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False), sa.Column("stable_key", sa.String(255), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False), sa.Column("title", sa.String(500), nullable=False), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("scope_json", JSONB(), nullable=False), sa.Column("effect_json", JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="EFFECTIVE"), sa.Column("valid_from", sa.DateTime(timezone=True)), sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("source_import_id", UUID(as_uuid=True), nullable=False), sa.Column("approved_by", UUID(as_uuid=True), nullable=False), sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("organization_id", "property_id", "stable_key", name="uq_property_rule_property_stable_key"),
        sa.UniqueConstraint("organization_id", "property_id", "id", name="uq_property_rule_organization_property_id"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_rule_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_rule_import_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "approved_by"], ["user.organization_id", "user.id"], name="fk_property_rule_approver_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION', 'RETRACTED')", name=op.f("ck_property_rule_status")),
        sa.CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name=op.f("ck_property_rule_validity")),
    )
    _indexes("property_rule", (("ix_property_rule_org_property", ("organization_id", "property_id")), ("ix_property_rule_source_import", ("source_import_id",)), ("ix_property_rule_approved_by", ("approved_by",))))

    op.create_table(
        "property_rule_relation", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False), sa.Column("source_rule_id", UUID(as_uuid=True), nullable=False), sa.Column("target_rule_id", UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(24), nullable=False), sa.Column("source_import_id", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint("source_rule_id", "target_rule_id", "relation_type", name="uq_property_rule_relation_edge"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_rule_relation_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "source_rule_id"], ["property_rule.organization_id", "property_rule.property_id", "property_rule.id"], name="fk_property_rule_relation_source_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "target_rule_id"], ["property_rule.organization_id", "property_rule.property_id", "property_rule.id"], name="fk_property_rule_relation_target_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_rule_relation_import_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("relation_type IN ('SUPERSEDES', 'SUPPLEMENTS', 'CONFLICTS')", name=op.f("ck_property_rule_relation_type")), sa.CheckConstraint("source_rule_id <> target_rule_id", name=op.f("ck_property_rule_relation_distinct_rules")),
    )
    _indexes("property_rule_relation", (("ix_property_rule_relation_org_property", ("organization_id", "property_id")), ("ix_property_rule_relation_source_rule", ("organization_id", "property_id", "source_rule_id")), ("ix_property_rule_relation_target_rule", ("organization_id", "property_id", "target_rule_id")), ("ix_property_rule_relation_source_import", ("source_import_id",))))

    op.create_table(
        "property_rule_source", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False), sa.Column("property_rule_id", UUID(as_uuid=True), nullable=False), sa.Column("property_document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("pdf_page", sa.Integer(), nullable=False), sa.Column("printed_page", sa.String(40)), sa.Column("section", sa.String(160)), sa.Column("quote", sa.Text(), nullable=False), sa.Column("field_path", sa.String(160)),
        sa.UniqueConstraint("property_rule_id", "property_document_id", "pdf_page", "field_path", name="uq_property_rule_source_location"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_rule_source_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "property_rule_id"], ["property_rule.organization_id", "property_rule.property_id", "property_rule.id"], name="fk_property_rule_source_rule_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "property_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_property_rule_source_document_property_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("pdf_page > 0", name=op.f("ck_property_rule_source_positive_page")), sa.CheckConstraint("btrim(quote) <> ''", name=op.f("ck_property_rule_source_nonblank_quote")),
    )
    _indexes("property_rule_source", (("ix_property_rule_source_org_property", ("organization_id", "property_id")), ("ix_property_rule_source_rule", ("organization_id", "property_id", "property_rule_id")), ("ix_property_rule_source_document", ("organization_id", "property_id", "property_document_id"))))

    op.create_table(
        "approved_fact_source", *_id_org_timestamps(),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False), sa.Column("subject_kind", sa.String(64), nullable=False), sa.Column("subject_id", UUID(as_uuid=True), nullable=False), sa.Column("field_key", sa.String(160), nullable=False),
        sa.Column("approved_value_json", JSONB(), nullable=False), sa.Column("value_fingerprint", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False, server_default="EFFECTIVE"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False), sa.Column("valid_to", sa.DateTime(timezone=True)), sa.Column("property_document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("pdf_page", sa.Integer(), nullable=False), sa.Column("printed_page", sa.String(40)), sa.Column("section", sa.String(160)), sa.Column("quote", sa.Text(), nullable=False), sa.Column("source_import_id", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint("subject_kind", "subject_id", "field_key", "value_fingerprint", "property_document_id", "pdf_page", "source_import_id", name="uq_approved_fact_source_evidence"),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_approved_fact_source_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "property_id", "property_document_id"], ["property_document.organization_id", "property_document.property_id", "property_document.id"], name="fk_approved_fact_source_document_property_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_approved_fact_source_import_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED')", name=op.f("ck_approved_fact_source_status")), sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name=op.f("ck_approved_fact_source_validity")),
        sa.CheckConstraint("(status = 'EFFECTIVE' AND valid_to IS NULL) OR (status = 'SUPERSEDED' AND valid_to IS NOT NULL)", name=op.f("ck_approved_fact_source_lifecycle")),
        sa.CheckConstraint("pdf_page > 0", name=op.f("ck_approved_fact_source_positive_page")), sa.CheckConstraint("btrim(quote) <> ''", name=op.f("ck_approved_fact_source_nonblank_quote")), sa.CheckConstraint("value_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_approved_fact_source_fingerprint")),
    )
    _indexes("approved_fact_source", (("ix_approved_fact_source_org_property", ("organization_id", "property_id")), ("ix_approved_fact_source_subject_field", ("subject_kind", "subject_id", "field_key")), ("ix_approved_fact_source_document", ("organization_id", "property_id", "property_document_id")), ("ix_approved_fact_source_source_import", ("source_import_id",))))

    for table in APPROVAL_TABLES:
        _add_rls(table)
    _triggers()
    _grants()

    op.add_column("declaration_import", sa.Column("approval_idempotency_key", sa.String(128), nullable=True))
    op.add_column("declaration_import", sa.Column("approval_result_json", JSONB(), nullable=True))
    op.execute(
        """
        UPDATE declaration_import
        SET approval_idempotency_key = 'legacy-0025-' || id::text,
            approval_result_json = jsonb_build_object(
                'legacy_migration', '0025_declaration_approval_backfill',
                'applied_property_id', applied_property_id::text
            )
        WHERE status = 'APPLIED'
        """
    )
    op.create_check_constraint(
        op.f("ck_declaration_import_approval_result_fields"), "declaration_import",
        "(status = 'APPLIED' AND approval_idempotency_key IS NOT NULL AND approval_result_json IS NOT NULL) "
        "OR (status <> 'APPLIED' AND approval_idempotency_key IS NULL AND approval_result_json IS NULL)",
    )
    op.add_column("declaration_candidate", sa.Column("corrected_value_json", JSONB(), nullable=True))
    op.add_column("declaration_candidate", sa.Column("review_comment", sa.Text(), nullable=True))
    # 0024 stored no separate correction value. When a proposal exists, it is
    # the only recoverable representation of a legacy CORRECTED decision.
    op.execute(
        """
        UPDATE declaration_candidate
        SET corrected_value_json = proposed_value_json
        WHERE review_status = 'CORRECTED'
          AND proposed_value_json IS NOT NULL
          AND proposed_value_json <> 'null'::jsonb
        """
    )
    # A 0024 correction without a proposal cannot be reconstructed safely.
    # Preserve the review trail but require a human to resolve it again.
    op.execute(
        """
        UPDATE declaration_candidate
        SET review_status = 'NEEDS_CLARIFICATION'
        WHERE review_status = 'CORRECTED'
          AND (
              proposed_value_json IS NULL
              OR proposed_value_json = 'null'::jsonb
          )
        """
    )
    op.create_check_constraint(op.f("ck_declaration_candidate_corrected_value"), "declaration_candidate", "(review_status = 'CORRECTED' AND corrected_value_json IS NOT NULL) OR (review_status <> 'CORRECTED' AND corrected_value_json IS NULL)")


def downgrade() -> None:
    # 0024 has nowhere to retain a corrected value. Do not silently turn an
    # actual correction into an accepted proposal after a future re-upgrade.
    op.execute(
        """
        UPDATE declaration_candidate
        SET review_status = 'NEEDS_CLARIFICATION', corrected_value_json = NULL
        WHERE review_status = 'CORRECTED'
        """
    )
    op.execute("ALTER TABLE declaration_candidate DROP CONSTRAINT IF EXISTS ck_declaration_candidate_corrected_value")
    # Compatibility for a local pre-release of this migration that let the
    # SQLAlchemy convention decorate an already-prefixed name.
    op.execute("ALTER TABLE declaration_candidate DROP CONSTRAINT IF EXISTS ck_declaration_candidate_ck_declaration_candidate_corrected_value")
    op.drop_column("declaration_candidate", "review_comment")
    op.drop_column("declaration_candidate", "corrected_value_json")
    op.execute("ALTER TABLE declaration_import DROP CONSTRAINT IF EXISTS ck_declaration_import_approval_result_fields")
    op.execute("ALTER TABLE declaration_import DROP CONSTRAINT IF EXISTS ck_declaration_import_ck_declaration_import_approval_result_fields")
    op.drop_column("declaration_import", "approval_result_json")
    op.drop_column("declaration_import", "approval_idempotency_key")
    for table in APPROVAL_TABLES:
        op.execute(f'REVOKE ALL PRIVILEGES ON TABLE "{table}" FROM app_rw')
        for action in ("select", "insert", "update", "delete"):
            op.execute(f'DROP POLICY IF EXISTS {table}_tenant_{action} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.execute("DROP TRIGGER IF EXISTS guard_approved_fact_source_update ON approved_fact_source")
    op.execute("DROP TRIGGER IF EXISTS guard_property_rule_update ON property_rule")
    op.execute("DROP FUNCTION IF EXISTS guard_approved_fact_source_update()")
    op.execute("DROP FUNCTION IF EXISTS guard_property_rule_update()")
    for table in reversed(APPROVAL_TABLES):
        op.drop_table(table)
    op.drop_constraint("uq_property_document_organization_property_id", "property_document")
    op.drop_constraint("uq_unit_organization_property_id", "unit")
