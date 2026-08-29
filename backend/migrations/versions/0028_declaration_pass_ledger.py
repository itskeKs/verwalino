"""Durable tenant-isolated declaration provider pass ledger.

Revision ID: 0028
Revises: 0027
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = '"declaration_import_pass"'
_TENANT_EXPRESSION = (
    "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
)


def _create_safe_metadata_validator() -> None:
    """Install the immutable DB boundary for app_rw's direct INSERT grant."""
    op.execute(
        r"""
        CREATE FUNCTION public.declaration_import_pass_safe_metadata_is_valid(
            metadata jsonb,
            row_attempt_no integer
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            input_tokens bigint;
            cached_input_tokens bigint;
            cache_write_input_tokens bigint;
            output_tokens bigint;
            latency_ms bigint;
            retry_count bigint;
            metadata_attempt_no bigint;
            reason jsonb;
        BEGIN
            IF metadata IS NULL OR jsonb_typeof(metadata) <> 'object'
               OR row_attempt_no IS NULL OR row_attempt_no < 1 THEN
                RETURN false;
            END IF;
            IF (SELECT count(*) FROM jsonb_object_keys(metadata)) <> 23
               OR NOT metadata ?& ARRAY[
                    'pass_id', 'attempt_no', 'stage', 'configured_model',
                    'returned_model_id', 'reasoning_effort', 'request_id',
                    'prompt_version', 'prompt_sha256', 'schema_version',
                    'schema_sha256', 'harness_version', 'harness_sha256',
                    'input_tokens', 'cached_input_tokens',
                    'cache_write_input_tokens', 'output_tokens',
                    'estimated_cost_usd', 'latency_ms', 'retry_count',
                    'route_action', 'route_reason_codes', 'failure_class'
               ] THEN
                RETURN false;
            END IF;

            IF jsonb_typeof(metadata->'pass_id') <> 'string'
               OR metadata->>'pass_id' NOT IN ('inventory', 'extract_core', 'extract_rules', 'reconcile', 'recheck')
               OR jsonb_typeof(metadata->'stage') <> 'string'
               OR metadata->>'stage' NOT IN ('provider', 'schema_repair')
               OR jsonb_typeof(metadata->'reasoning_effort') <> 'string'
               OR metadata->>'reasoning_effort' NOT IN ('none', 'low', 'medium', 'high', 'xhigh', 'max')
               OR jsonb_typeof(metadata->'route_action') <> 'string'
               OR metadata->>'route_action' NOT IN ('NONE', 'VISION_RECHECK', 'HUMAN_REVIEW') THEN
                RETURN false;
            END IF;

            IF jsonb_typeof(metadata->'configured_model') <> 'string'
               OR char_length(metadata->>'configured_model') > 128
               OR metadata->>'configured_model' !~ '^[A-Za-z0-9._:-]+$'
               OR jsonb_typeof(metadata->'prompt_version') <> 'string'
               OR char_length(metadata->>'prompt_version') > 128
               OR metadata->>'prompt_version' !~ '^[A-Za-z0-9._:-]+$'
               OR jsonb_typeof(metadata->'schema_version') <> 'string'
               OR char_length(metadata->>'schema_version') > 128
               OR metadata->>'schema_version' !~ '^[A-Za-z0-9._:-]+$'
               OR jsonb_typeof(metadata->'harness_version') <> 'string'
               OR char_length(metadata->>'harness_version') > 128
               OR metadata->>'harness_version' !~ '^[A-Za-z0-9._:-]+$' THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(metadata->'returned_model_id') NOT IN ('null', 'string')
               OR (jsonb_typeof(metadata->'returned_model_id') = 'string' AND (
                    char_length(metadata->>'returned_model_id') > 128
                    OR metadata->>'returned_model_id' !~ '^[A-Za-z0-9._:-]+$'))
               OR jsonb_typeof(metadata->'request_id') NOT IN ('null', 'string')
               OR (jsonb_typeof(metadata->'request_id') = 'string' AND (
                    char_length(metadata->>'request_id') > 256
                    OR metadata->>'request_id' !~ '^[A-Za-z0-9._:-]+$'))
               OR jsonb_typeof(metadata->'failure_class') NOT IN ('null', 'string')
               OR (jsonb_typeof(metadata->'failure_class') = 'string' AND
                    metadata->>'failure_class' !~ '^[A-Z][A-Z0-9_]{0,99}$') THEN
                RETURN false;
            END IF;

            IF jsonb_typeof(metadata->'prompt_sha256') <> 'string'
               OR metadata->>'prompt_sha256' !~ '^[0-9a-f]{64}$'
               OR jsonb_typeof(metadata->'schema_sha256') <> 'string'
               OR metadata->>'schema_sha256' !~ '^[0-9a-f]{64}$'
               OR jsonb_typeof(metadata->'harness_sha256') <> 'string'
               OR metadata->>'harness_sha256' !~ '^[0-9a-f]{64}$' THEN
                RETURN false;
            END IF;

            IF jsonb_typeof(metadata->'attempt_no') <> 'number'
               OR metadata->>'attempt_no' !~ '^[1-9][0-9]{0,9}$'
               OR (metadata->>'attempt_no')::bigint <> row_attempt_no
               OR jsonb_typeof(metadata->'input_tokens') <> 'number'
               OR metadata->>'input_tokens' !~ '^(0|[1-9][0-9]{0,9})$'
               OR jsonb_typeof(metadata->'cached_input_tokens') <> 'number'
               OR metadata->>'cached_input_tokens' !~ '^(0|[1-9][0-9]{0,9})$'
               OR jsonb_typeof(metadata->'cache_write_input_tokens') <> 'number'
               OR metadata->>'cache_write_input_tokens' !~ '^(0|[1-9][0-9]{0,9})$'
               OR jsonb_typeof(metadata->'output_tokens') <> 'number'
               OR metadata->>'output_tokens' !~ '^(0|[1-9][0-9]{0,9})$'
               OR jsonb_typeof(metadata->'latency_ms') <> 'number'
               OR metadata->>'latency_ms' !~ '^(0|[1-9][0-9]{0,9})$'
               OR jsonb_typeof(metadata->'retry_count') <> 'number'
               OR metadata->>'retry_count' !~ '^(0|[1-9][0-9]{0,9})$' THEN
                RETURN false;
            END IF;
            input_tokens := (metadata->>'input_tokens')::bigint;
            cached_input_tokens := (metadata->>'cached_input_tokens')::bigint;
            cache_write_input_tokens := (metadata->>'cache_write_input_tokens')::bigint;
            output_tokens := (metadata->>'output_tokens')::bigint;
            latency_ms := (metadata->>'latency_ms')::bigint;
            retry_count := (metadata->>'retry_count')::bigint;
            metadata_attempt_no := (metadata->>'attempt_no')::bigint;
            IF metadata_attempt_no > 1000000000
               OR input_tokens > 1000000000
               OR cached_input_tokens > 1000000000
               OR cache_write_input_tokens > 1000000000
               OR output_tokens > 1000000000
               OR latency_ms > 1000000000
               OR retry_count > 1000000000 THEN
                RETURN false;
            END IF;
            IF cached_input_tokens + cache_write_input_tokens > input_tokens THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(metadata->'estimated_cost_usd') <> 'string'
               OR metadata->>'estimated_cost_usd' !~ '^(0|[0-9]+(\.[0-9]+)?)([eE][+-]?[0-9]+)?$' THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(metadata->'route_reason_codes') <> 'array' THEN
                RETURN false;
            END IF;
            FOR reason IN SELECT value FROM jsonb_array_elements(metadata->'route_reason_codes')
            LOOP
                IF jsonb_typeof(reason) <> 'string'
                   OR reason #>> '{}' NOT IN (
                        'PROVIDER_REFUSAL', 'PROVIDER_INCOMPLETE', 'PROVIDER_TIMEOUT',
                        'PROVIDER_SCHEMA_FAILURE', 'UNKNOWN_EVIDENCE_DOCUMENT',
                        'EVIDENCE_PAGE_OUT_OF_RANGE', 'EVIDENCE_QUOTE_NOT_FOUND',
                        'LOW_PAGE_QUALITY', 'DOMAIN_BLOCKER'
                   ) THEN
                    RETURN false;
                END IF;
            END LOOP;
            RETURN true;
        EXCEPTION WHEN others THEN
            RETURN false;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _create_safe_metadata_validator()
    op.create_unique_constraint(
        "uq_declaration_extraction_run_organization_id_id",
        "declaration_extraction_run",
        ["organization_id", "id"],
    )
    op.create_table(
        "declaration_import_pass",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id", UUID(as_uuid=True),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("declaration_extraction_run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("safe_metadata", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "declaration_extraction_run_id"],
            ["declaration_extraction_run.organization_id", "declaration_extraction_run.id"],
            name="fk_declaration_import_pass_run_tenant", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id", "declaration_extraction_run_id", "attempt_no",
            name="uq_declaration_import_pass_run_attempt",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_declaration_import_pass_positive_attempt"),
        sa.CheckConstraint(
            "declaration_import_pass_safe_metadata_is_valid(safe_metadata, attempt_no)",
            name="ck_declaration_import_pass_safe_metadata",
        ),
    )
    op.create_index(
        "ix_declaration_import_pass_org_run_attempt",
        "declaration_import_pass",
        ["organization_id", "declaration_extraction_run_id", "attempt_no"],
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY declaration_import_pass_tenant_select ON {_TABLE} "
        f"FOR SELECT USING ({_TENANT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY declaration_import_pass_tenant_insert ON {_TABLE} "
        f"FOR INSERT WITH CHECK ({_TENANT_EXPRESSION})"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM app_rw")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO app_rw")


def downgrade() -> None:
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM app_rw")
    op.execute(f"DROP POLICY IF EXISTS declaration_import_pass_tenant_insert ON {_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS declaration_import_pass_tenant_select ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_declaration_import_pass_org_run_attempt", table_name="declaration_import_pass")
    op.drop_table("declaration_import_pass")
    op.execute(
        "DROP FUNCTION IF EXISTS public.declaration_import_pass_safe_metadata_is_valid(jsonb, integer)"
    )
    op.drop_constraint(
        "uq_declaration_extraction_run_organization_id_id",
        "declaration_extraction_run",
        type_="unique",
    )
