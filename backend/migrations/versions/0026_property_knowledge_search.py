"""Indexed approved-property knowledge projections and Q&A accounting.

Revision ID: 0026
Revises: 0025
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KNOWN_FACT_FIELDS = {
    "PROPERTY": {"name", "street", "zip_code", "city", "management_type", "mea_denominator"},
    "UNIT": {"number", "location", "building_part", "area", "heated_area", "usage_type", "mea"},
    "LEGAL_REFERENCE": {"reference_type", "district", "cadastral_district", "field_number", "parcel", "sheet", "details"},
    "ASSIGNMENT": {"assignment_type", "designation", "location", "description", "unit_subject_key", "details"},
    "DOCUMENT_RELATION": {"relation"},
    # Plan 2 deliberately persists one generic, source-backed fact row per rule.
    # It is a supported historical record, not an unknown extraction field.
    "RULE": {"rule"},
}


def _reconcile_property_mea_scale_schema_drift() -> None:
    """Reconcile the one known old-schema state incorrectly stamped as 0023.

    Production fully applied a separate auth migration that reused revision
    ``0023``; Alembic therefore skipped this repository's Property/MEA 0023
    migration. This migration accepts either the exact completed Property/MEA
    contract or that exact preceding 0022 contract. Only the latter is repaired,
    atomically and without deleting or rewriting rows. Any partial or
    incompatible schema is ambiguous and therefore fails closed.
    """
    op.execute(
        """
        DO $repair$
        DECLARE
            full_contract boolean;
            old_contract boolean;
        BEGIN
            SELECT
                (
                    SELECT attribute.atttypid = 'pg_catalog.int8'::regtype
                           AND NOT attribute.attnotnull
                           AND NOT attribute.atthasdef
                    FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = 'public.property'::regclass
                      AND attribute.attname = 'mea_denominator'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND (
                    SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) = 'character varying(24)'
                           AND attribute.attnotnull
                           AND pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid) =
                               pg_catalog.quote_literal('LEGACY_UNCONFIRMED') || '::character varying'
                    FROM pg_catalog.pg_attribute AS attribute
                    LEFT JOIN pg_catalog.pg_attrdef AS default_value
                      ON default_value.adrelid = attribute.attrelid
                     AND default_value.adnum = attribute.attnum
                    WHERE attribute.attrelid = 'public.property'::regclass
                      AND attribute.attname = 'mea_scale_status'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND (
                    SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) = 'numeric(19,6)'
                           AND NOT attribute.attnotnull
                           AND NOT attribute.atthasdef
                    FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = 'public.unit'::regclass
                      AND attribute.attname = 'mea'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND (
                    SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) = 'character varying(255)'
                           AND attribute.attnotnull
                           AND pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid) =
                               pg_catalog.quote_literal('') || '::character varying'
                    FROM pg_catalog.pg_attribute AS attribute
                    LEFT JOIN pg_catalog.pg_attrdef AS default_value
                      ON default_value.adrelid = attribute.attrelid
                     AND default_value.adnum = attribute.attnum
                    WHERE attribute.attrelid = 'public.unit'::regclass
                      AND attribute.attname = 'building_part'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND EXISTS (
                    SELECT 1 FROM pg_catalog.pg_constraint AS check_constraint
                    WHERE check_constraint.conrelid = 'public.property'::regclass
                      AND check_constraint.conname = 'ck_property_mea_scale_status'
                      AND check_constraint.contype = 'c'
                      AND check_constraint.convalidated
                      AND pg_catalog.pg_get_constraintdef(check_constraint.oid) =
                          $constraint$CHECK (((mea_scale_status)::text = ANY ((ARRAY['CONFIRMED'::character varying, 'LEGACY_UNCONFIRMED'::character varying])::text[])))$constraint$
                )
                AND EXISTS (
                    SELECT 1 FROM pg_catalog.pg_constraint AS check_constraint
                    WHERE check_constraint.conrelid = 'public.property'::regclass
                      AND check_constraint.conname = 'ck_property_mea_scale_consistency'
                      AND check_constraint.contype = 'c'
                      AND check_constraint.convalidated
                      AND pg_catalog.pg_get_constraintdef(check_constraint.oid) =
                          $constraint$CHECK (((((mea_scale_status)::text = 'LEGACY_UNCONFIRMED'::text) AND (mea_denominator IS NULL)) OR (((mea_scale_status)::text = 'CONFIRMED'::text) AND ((mea_denominator >= 1) AND (mea_denominator <= '1000000000000'::bigint)))))$constraint$
                )
                AND EXISTS (
                    SELECT 1 FROM pg_catalog.pg_constraint AS check_constraint
                    WHERE check_constraint.conrelid = 'public.unit'::regclass
                      AND check_constraint.conname = 'ck_unit_mea_nonnegative'
                      AND check_constraint.contype = 'c'
                      AND NOT check_constraint.convalidated
                      AND pg_catalog.pg_get_constraintdef(check_constraint.oid) =
                          $constraint$CHECK (((mea IS NULL) OR (mea >= (0)::numeric))) NOT VALID$constraint$
                )
            INTO full_contract;

            SELECT
                NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = 'public.property'::regclass
                      AND attribute.attname IN ('mea_denominator', 'mea_scale_status')
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND (
                    SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) = 'numeric(12,6)'
                           AND NOT attribute.attnotnull
                           AND NOT attribute.atthasdef
                    FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = 'public.unit'::regclass
                      AND attribute.attname = 'mea'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_attribute AS attribute
                    WHERE attribute.attrelid = 'public.unit'::regclass
                      AND attribute.attname = 'building_part'
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                )
                AND NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_constraint AS check_constraint
                    WHERE check_constraint.conrelid IN ('public.property'::regclass, 'public.unit'::regclass)
                      AND check_constraint.conname IN (
                          'ck_property_mea_scale_status',
                          'ck_property_mea_scale_consistency',
                          'ck_unit_mea_nonnegative'
                      )
                )
            INTO old_contract;

            IF full_contract THEN
                RETURN;
            END IF;

            IF NOT old_contract THEN
                RAISE EXCEPTION 'incompatible property/MEA schema; refusing automatic 0023 repair';
            END IF;

            ALTER TABLE public.property
                ADD COLUMN mea_denominator bigint,
                ADD COLUMN mea_scale_status varchar(24) NOT NULL DEFAULT 'LEGACY_UNCONFIRMED';
            ALTER TABLE public.property
                ADD CONSTRAINT ck_property_mea_scale_status
                    CHECK (mea_scale_status IN ('CONFIRMED','LEGACY_UNCONFIRMED')),
                ADD CONSTRAINT ck_property_mea_scale_consistency
                    CHECK (
                        (mea_scale_status = 'LEGACY_UNCONFIRMED' AND mea_denominator IS NULL)
                        OR (mea_scale_status = 'CONFIRMED' AND mea_denominator BETWEEN 1 AND 1000000000000)
                    );
            ALTER TABLE public.unit
                ALTER COLUMN mea TYPE numeric(19,6),
                ADD COLUMN building_part varchar(255) NOT NULL DEFAULT '',
                ADD CONSTRAINT ck_unit_mea_nonnegative
                    CHECK (mea IS NULL OR mea >= 0) NOT VALID;
        END;
        $repair$;
        """
    )


def _value_display_sql(value: str = "afs.approved_value_json") -> str:
    return f"""
        CASE {value}->>'kind'
            WHEN 'MEA' THEN concat(
                replace(to_char(({value}->>'numerator')::numeric, 'FM999,999,999,999,999,999,999'), ',', '.'),
                '/',
                replace(to_char(({value}->>'denominator')::numeric, 'FM999,999,999,999,999,999,999'), ',', '.')
            )
            WHEN 'FRACTION' THEN concat(
                replace(to_char(({value}->>'numerator')::numeric, 'FM999,999,999,999,999,999,999'), ',', '.'),
                '/',
                replace(to_char(({value}->>'denominator')::numeric, 'FM999,999,999,999,999,999,999'), ',', '.')
            )
            WHEN 'DECIMAL' THEN concat_ws(' ', {value}->>'value', {value}->>'unit')
            WHEN 'DATE' THEN {value}->>'value'
            WHEN 'BOOLEAN' THEN CASE WHEN ({value}->>'value')::boolean THEN 'Ja' ELSE 'Nein' END
            WHEN 'INTEGER' THEN {value}->>'value'
            WHEN 'PERCENTAGE' THEN concat({value}->>'value', ' Prozent')
            WHEN 'TEXT' THEN {value}->>'value'
            ELSE {value}::text
        END
    """


def _assert_supported_existing_fact_sources() -> None:
    clauses = " OR ".join(
        "(subject_kind = '{kind}' AND field_key IN ({fields}))".format(
            kind=kind,
            fields=", ".join("'{}'".format(field) for field in sorted(fields)),
        )
        for kind, fields in sorted(_KNOWN_FACT_FIELDS.items())
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM approved_fact_source
                WHERE NOT ({clauses})
            ) THEN
                RAISE EXCEPTION 'approved_fact_source has unsupported subject_kind/field_key for 0026 search projection';
            END IF;
        END;
        $$;
        """
    )


def _backfill_fact_search_text() -> None:
    display = _value_display_sql()
    quote = "left(regexp_replace(afs.quote, '\\s+', ' ', 'g'), 500)"
    # Each branch uses the immutable source snapshot for the value. The joined
    # canonical row supplies only a stable, human-readable subject label.
    op.execute(
        f"""
        UPDATE approved_fact_source afs
        SET search_text = btrim(concat_ws(' ',
            'Objekt', p.name, p.street, p.zip_code, p.city,
            CASE afs.field_key
                WHEN 'name' THEN 'Objektname' WHEN 'street' THEN 'Straße'
                WHEN 'zip_code' THEN 'Postleitzahl' WHEN 'city' THEN 'Ort'
                WHEN 'management_type' THEN 'Verwaltungsart'
                WHEN 'mea_denominator' THEN 'MEA Nenner' END,
            {display}, afs.section, {quote}))
        FROM property p
        WHERE afs.subject_kind = 'PROPERTY' AND p.id = afs.subject_id
          AND p.id = afs.property_id AND p.organization_id = afs.organization_id
        """
    )

    op.execute(
        f"""
        UPDATE approved_fact_source afs
        SET search_text = btrim(concat_ws(' ',
            'Einheit', u.number, u.location, u.building_part,
            CASE afs.field_key
                WHEN 'number' THEN 'Einheitsnummer' WHEN 'location' THEN 'Lage'
                WHEN 'building_part' THEN 'Gebäudeteil' WHEN 'area' THEN 'Fläche'
                WHEN 'heated_area' THEN 'Beheizte Fläche' WHEN 'usage_type' THEN 'Nutzungsart'
                WHEN 'mea' THEN 'Miteigentumsanteil' END,
            {display}, afs.section, {quote}))
        FROM unit u
        WHERE afs.subject_kind = 'UNIT' AND u.id = afs.subject_id
          AND u.organization_id = afs.organization_id AND u.property_id = afs.property_id
        """
    )
    op.execute(
        f"""
        UPDATE approved_fact_source afs
        SET search_text = btrim(concat_ws(' ',
            'Rechtsreferenz', lr.reference_type, lr.district, lr.cadastral_district,
            CASE afs.field_key
                WHEN 'reference_type' THEN 'Referenztyp' WHEN 'district' THEN 'Amtsgericht'
                WHEN 'cadastral_district' THEN 'Gemarkung' WHEN 'field_number' THEN 'Flur'
                WHEN 'parcel' THEN 'Flurstück' WHEN 'sheet' THEN 'Blatt' WHEN 'details' THEN 'Details' END,
            {display}, afs.section, {quote}))
        FROM property_legal_reference lr
        WHERE afs.subject_kind = 'LEGAL_REFERENCE' AND lr.id = afs.subject_id
          AND lr.organization_id = afs.organization_id AND lr.property_id = afs.property_id
        """
    )
    op.execute(
        f"""
        UPDATE approved_fact_source afs
        SET search_text = btrim(concat_ws(' ',
            'Zuordnung', pa.designation, pa.assignment_type, pa.location,
            CASE afs.field_key
                WHEN 'assignment_type' THEN 'Zuordnungsart' WHEN 'designation' THEN 'Bezeichnung'
                WHEN 'location' THEN 'Lage' WHEN 'description' THEN 'Beschreibung'
                WHEN 'unit_subject_key' THEN 'Einheit' WHEN 'details' THEN 'Details' END,
            {display}, afs.section, {quote}))
        FROM property_assignment pa
        WHERE afs.subject_kind = 'ASSIGNMENT' AND pa.id = afs.subject_id
          AND pa.organization_id = afs.organization_id AND pa.property_id = afs.property_id
        """
    )
    op.execute(
        f"""
        UPDATE approved_fact_source afs
        SET search_text = btrim(concat_ws(' ',
            'Dokumentbeziehung', dr.relation_type,
            'Beziehung', {display}, afs.section, {quote}))
        FROM property_document_relation dr
        WHERE afs.subject_kind = 'DOCUMENT_RELATION' AND dr.id = afs.subject_id
          AND dr.organization_id = afs.organization_id AND dr.property_id = afs.property_id
        """
    )
    op.execute(
        f"""
        UPDATE approved_fact_source afs
        SET search_text = btrim(concat_ws(' ',
            'Regel', pr.topic, pr.title, pr.summary,
            pr.scope_json::text, pr.effect_json::text, afs.section, {quote}))
        FROM property_rule pr
        WHERE afs.subject_kind = 'RULE' AND afs.field_key = 'rule' AND pr.id = afs.subject_id
          AND pr.organization_id = afs.organization_id AND pr.property_id = afs.property_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM approved_fact_source WHERE btrim(search_text) = '') THEN
                RAISE EXCEPTION 'approved_fact_source 0026 search projection backfill left an empty row';
            END IF;
        END;
        $$;
        """
    )


def _backfill_rule_search_text() -> None:
    op.execute(
        """
        UPDATE property_rule pr
        SET search_text = left(btrim(concat_ws(' ',
            pr.topic, pr.title, pr.summary, pr.scope_json::text, pr.effect_json::text,
            (
                SELECT string_agg(
                btrim(concat_ws(' ', source.section,
                    left(regexp_replace(source.quote, '\\s+', ' ', 'g'), 500)
                )),
                ' ' ORDER BY source.property_document_id, source.pdf_page, source.id
                )
                FROM property_rule_source source
                WHERE source.organization_id = pr.organization_id
                  AND source.property_id = pr.property_id
                  AND source.property_rule_id = pr.id
            )
        )), 100000)
        """
    )


def _add_query_run_rls() -> None:
    table = '"property_knowledge_query_run"'
    expression = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY property_knowledge_query_run_tenant_select ON {table} FOR SELECT USING ({expression})")
    op.execute(f"CREATE POLICY property_knowledge_query_run_tenant_insert ON {table} FOR INSERT WITH CHECK ({expression})")
    op.execute(f"CREATE POLICY property_knowledge_query_run_tenant_update ON {table} FOR UPDATE USING ({expression}) WITH CHECK ({expression})")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM app_rw")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO app_rw")


def _add_query_run_lifecycle_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_property_knowledge_query_run_insert() RETURNS trigger AS $$
        BEGIN
            IF NEW.status <> 'RESERVED'
               OR NEW.estimated_cost_usd IS DISTINCT FROM 0
               OR NEW.input_tokens IS DISTINCT FROM 0
               OR NEW.output_tokens IS DISTINCT FROM 0
               OR NEW.latency_ms IS NOT NULL
               OR NEW.outcome IS NOT NULL
               OR NEW.error_code IS NOT NULL
               OR NEW.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'property_knowledge_query_run insert requires a clean RESERVED reservation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_property_knowledge_query_run_insert
        BEFORE INSERT ON property_knowledge_query_run
        FOR EACH ROW EXECUTE FUNCTION guard_property_knowledge_query_run_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_property_knowledge_query_run_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.property_id IS DISTINCT FROM OLD.property_id
               OR NEW.budget_window_start IS DISTINCT FROM OLD.budget_window_start
               OR NEW.provider IS DISTINCT FROM OLD.provider
               OR NEW.model IS DISTINCT FROM OLD.model
               OR NEW.reserved_cost_usd IS DISTINCT FROM OLD.reserved_cost_usd
               OR NEW.reservation_expires_at IS DISTINCT FROM OLD.reservation_expires_at
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'property_knowledge_query_run immutable reservation fields';
            END IF;
            IF OLD.status <> 'RESERVED' OR NEW.status NOT IN ('FINALIZED', 'EXPIRED') THEN
                RAISE EXCEPTION 'property_knowledge_query_run only permits RESERVED to FINALIZED or EXPIRED';
            END IF;
            IF NEW.finalized_at IS NULL THEN
                RAISE EXCEPTION 'property_knowledge_query_run finalization timestamp is required';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_property_knowledge_query_run_update
        BEFORE UPDATE ON property_knowledge_query_run
        FOR EACH ROW EXECUTE FUNCTION guard_property_knowledge_query_run_update()
        """
    )


def upgrade() -> None:
    _reconcile_property_mea_scale_schema_drift()
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column("approved_fact_source", sa.Column("search_text", sa.Text(), nullable=False, server_default=""))
    _assert_supported_existing_fact_sources()
    _backfill_fact_search_text()
    _backfill_rule_search_text()
    op.add_column(
        "approved_fact_source",
        sa.Column("search_vector", postgresql.TSVECTOR(), sa.Computed("to_tsvector('german'::regconfig, coalesce(search_text, ''))", persisted=True), nullable=False),
    )
    op.add_column(
        "property_rule",
        sa.Column("search_vector", postgresql.TSVECTOR(), sa.Computed("to_tsvector('german'::regconfig, coalesce(search_text, ''))", persisted=True), nullable=False),
    )
    op.create_index("ix_approved_fact_source_search_vector", "approved_fact_source", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_property_rule_search_vector", "property_rule", ["search_vector"], postgresql_using="gin")
    op.execute("CREATE INDEX ix_approved_fact_source_search_trgm ON approved_fact_source USING gin (lower(search_text) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_property_rule_search_trgm ON property_rule USING gin (lower(search_text) gin_trgm_ops)")

    op.create_table(
        "property_knowledge_query_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("property_id", UUID(as_uuid=True), nullable=False),
        sa.Column("budget_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="RESERVED"),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer()), sa.Column("outcome", sa.String(64)), sa.Column("error_code", sa.String(100)),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_knowledge_query_run_property_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('RESERVED', 'FINALIZED', 'EXPIRED')", name="ck_property_knowledge_query_run_status"),
        sa.CheckConstraint("reserved_cost_usd >= 0", name="ck_property_knowledge_query_run_reserved_cost_nonnegative"),
        sa.CheckConstraint("estimated_cost_usd >= 0", name="ck_property_knowledge_query_run_estimated_cost_nonnegative"),
        sa.CheckConstraint("input_tokens >= 0", name="ck_property_knowledge_query_run_input_tokens_nonnegative"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_property_knowledge_query_run_output_tokens_nonnegative"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_property_knowledge_query_run_latency_nonnegative"),
    )
    op.create_index("ix_property_knowledge_query_run_org_property", "property_knowledge_query_run", ["organization_id", "property_id"])
    op.create_index("ix_property_knowledge_query_run_org_window_status", "property_knowledge_query_run", ["organization_id", "budget_window_start", "status"])
    _add_query_run_rls()
    _add_query_run_lifecycle_trigger()


def downgrade() -> None:
    table = '"property_knowledge_query_run"'
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM app_rw")
    for action in ("select", "insert", "update"):
        op.execute(f"DROP POLICY IF EXISTS property_knowledge_query_run_tenant_{action} ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS guard_property_knowledge_query_run_update ON property_knowledge_query_run")
    op.execute("DROP TRIGGER IF EXISTS guard_property_knowledge_query_run_insert ON property_knowledge_query_run")
    op.execute("DROP FUNCTION IF EXISTS guard_property_knowledge_query_run_update()")
    op.execute("DROP FUNCTION IF EXISTS guard_property_knowledge_query_run_insert()")
    op.drop_table("property_knowledge_query_run")
    op.execute("DROP INDEX IF EXISTS ix_approved_fact_source_search_trgm")
    op.execute("DROP INDEX IF EXISTS ix_property_rule_search_trgm")
    op.drop_index("ix_approved_fact_source_search_vector", table_name="approved_fact_source")
    op.drop_index("ix_property_rule_search_vector", table_name="property_rule")
    op.drop_column("approved_fact_source", "search_vector")
    op.drop_column("approved_fact_source", "search_text")
    op.drop_column("property_rule", "search_vector")
