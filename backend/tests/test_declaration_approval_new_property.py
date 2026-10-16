"""Persistence contract for approved declaration facts and their provenance."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import psycopg2
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.models import Base
from app.models.approved_fact_source import ApprovedFactSource
from app.models.audit_event import AuditEvent
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.models.declaration_import import DeclarationImport, DeclarationImportDocument
from app.models.property import Property
from app.models.property_assignment import PropertyAssignment
from app.models.property_document import PropertyDocument
from app.models.property_document_relation import PropertyDocumentRelation
from app.models.property_legal_reference import PropertyLegalReference
from app.models.property_rule import PropertyRule, PropertyRuleSource
from app.models.unit import Unit
from app.declarations.pipeline import _candidate_drafts
from app.declarations.schemas import (
    AdditionalFactPayload,
    DocumentInventoryOutput,
    ReconciliationOutput,
    RulePayload,
)


APPROVAL_TABLES = frozenset(
    {
        "property_legal_reference",
        "property_assignment",
        "property_document_relation",
        "property_rule",
        "property_rule_relation",
        "property_rule_source",
        "approved_fact_source",
    }
)

UPDATE_COLUMNS = {
    "property_legal_reference": {"status", "valid_to", "updated_at"},
    "property_assignment": {"status", "valid_to", "updated_at"},
    "property_document_relation": {"status", "updated_at"},
    "property_rule": {"status", "valid_to", "search_text", "updated_at"},
    "approved_fact_source": {"status", "valid_to", "updated_at"},
    "property_rule_relation": set(),
    "property_rule_source": set(),
}

EXPECTED_INDEXES = {
    "property_legal_reference": {"ix_property_legal_reference_org_property", "ix_property_legal_reference_source_import"},
    "property_assignment": {"ix_property_assignment_org_property", "ix_property_assignment_unit_property", "ix_property_assignment_source_import"},
    "property_document_relation": {"ix_property_document_relation_org_property", "ix_property_document_relation_source_document", "ix_property_document_relation_target_document", "ix_property_document_relation_source_import"},
    "property_rule": {"ix_property_rule_org_property", "ix_property_rule_source_import", "ix_property_rule_approved_by"},
    "property_rule_relation": {"ix_property_rule_relation_org_property", "ix_property_rule_relation_source_rule", "ix_property_rule_relation_target_rule", "ix_property_rule_relation_source_import"},
    "property_rule_source": {"ix_property_rule_source_org_property", "ix_property_rule_source_rule", "ix_property_rule_source_document"},
    "approved_fact_source": {"ix_approved_fact_source_org_property", "ix_approved_fact_source_subject_field", "ix_approved_fact_source_document", "ix_approved_fact_source_source_import"},
}

EXPECTED_CHECKS = {
    "property_legal_reference": {"ck_property_legal_reference_status", "ck_property_legal_reference_validity"},
    "property_assignment": {"ck_property_assignment_status", "ck_property_assignment_validity"},
    "property_document_relation": {
        "ck_property_document_relation_type",
        "ck_property_document_relation_status",
        "ck_property_document_relation_distinct_documents",
    },
    "property_rule": {"ck_property_rule_status", "ck_property_rule_validity"},
    "property_rule_relation": {"ck_property_rule_relation_type", "ck_property_rule_relation_distinct_rules"},
    "property_rule_source": {"ck_property_rule_source_positive_page", "ck_property_rule_source_nonblank_quote"},
    "approved_fact_source": {
        "ck_approved_fact_source_status",
        "ck_approved_fact_source_validity",
        "ck_approved_fact_source_lifecycle",
        "ck_approved_fact_source_positive_page",
        "ck_approved_fact_source_nonblank_quote",
        "ck_approved_fact_source_fingerprint",
    },
}


def _declaration_alembic_config() -> Config:
    config = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config.set_main_option("sqlalchemy.url", os.environ["HV_DATABASE_URL_SYNC"])
    config.attributes["hv_declaration_test_database"] = True
    config.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "migrations")
    )
    return config


async def _set_org(session, organization_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


async def _insert_approval_graph(session, organization_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Insert a dependency-valid row for every 0025 table through app_rw."""
    ids = {name: uuid.uuid4() for name in (
        "property", "unit", "document_a", "document_b", "import", "legal",
        "assignment", "document_relation", "rule_a", "rule_b", "rule_relation",
        "rule_source", "fact_source",
    )}
    await session.execute(text("""
        INSERT INTO property (id, organization_id, name, management_type)
        VALUES (:id, :org, 'Approval graph', 'WEG')
    """), {"id": ids["property"], "org": organization_id})
    await session.execute(text("""
        INSERT INTO unit (id, organization_id, property_id, number, usage_type)
        VALUES (:id, :org, :property_id, '1', 'WOHNUNG')
    """), {"id": ids["unit"], "org": organization_id, "property_id": ids["property"]})
    for key, title in (("document_a", "Declaration"), ("document_b", "Amendment")):
        await session.execute(text("""
            INSERT INTO property_document (
                id, organization_id, property_id, doc_type, title, original_filename,
                mime_type, content_byte_size, content_sha256, storage_uri,
                storage_backend, page_count, uploaded_by
            ) VALUES (
                :id, :org, :property_id, 'DECLARATION', :title, :filename,
                'application/pdf', 1, :sha, :uri, 'LOCAL', 1, :user_id
            )
        """), {"id": ids[key], "org": organization_id, "property_id": ids["property"], "title": title,
                  "filename": f"{key}.pdf", "sha": "a" * 64 if key == "document_a" else "b" * 64,
                  "uri": f"documents/{ids[key]}", "user_id": user_id})
    await session.execute(text("""
        INSERT INTO declaration_import (
            id, organization_id, mode, target_property_id, status, initiated_by, max_cost_usd
        ) VALUES (:id, :org, 'EXISTING_PROPERTY', :property_id, 'DRAFT', :user_id, 1)
    """), {"id": ids["import"], "org": organization_id, "property_id": ids["property"], "user_id": user_id})
    common = {"org": organization_id, "property_id": ids["property"], "import_id": ids["import"], "unit_id": ids["unit"], "user_id": user_id}
    await session.execute(text("""
        INSERT INTO property_legal_reference (id, organization_id, property_id, reference_type, details_json, source_import_id)
        VALUES (:id, :org, :property_id, 'LAND_REGISTER', '{}'::jsonb, :import_id)
    """), {**common, "id": ids["legal"]})
    await session.execute(text("""
        INSERT INTO property_assignment (id, organization_id, property_id, unit_id, stable_key, assignment_type, designation, details_json, source_import_id)
        VALUES (:id, :org, :property_id, :unit_id, 'assignment:1', 'SPECIAL_USE', 'Parking', '{}'::jsonb, :import_id)
    """), {**common, "id": ids["assignment"]})
    await session.execute(text("""
        INSERT INTO property_document_relation (id, organization_id, property_id, source_document_id, target_document_id, relation_type, source_import_id)
        VALUES (:id, :org, :property_id, :source_document_id, :target_document_id, 'AMENDS', :import_id)
    """), {**common, "id": ids["document_relation"], "source_document_id": ids["document_b"], "target_document_id": ids["document_a"]})
    for key, stable_key in (("rule_a", "rule:heating-v1"), ("rule_b", "rule:heating-v2")):
        await session.execute(text("""
            INSERT INTO property_rule (
                id, organization_id, property_id, stable_key, topic, title, summary,
                scope_json, effect_json, source_import_id, approved_by, approved_at
            ) VALUES (
                :id, :org, :property_id, :stable_key, 'heating_costs', :title, 'Rule',
                '{}'::jsonb, '{}'::jsonb, :import_id, :user_id, now()
            )
        """), {**common, "id": ids[key], "stable_key": stable_key, "title": stable_key})
    await session.execute(text("""
        INSERT INTO property_rule_relation (id, organization_id, property_id, source_rule_id, target_rule_id, relation_type, source_import_id)
        VALUES (:id, :org, :property_id, :source_rule_id, :target_rule_id, 'SUPERSEDES', :import_id)
    """), {**common, "id": ids["rule_relation"], "source_rule_id": ids["rule_b"], "target_rule_id": ids["rule_a"]})
    await session.execute(text("""
        INSERT INTO property_rule_source (id, organization_id, property_id, property_rule_id, property_document_id, pdf_page, quote)
        VALUES (:id, :org, :property_id, :rule_id, :document_id, 1, '§ 8 Absatz 5')
    """), {**common, "id": ids["rule_source"], "rule_id": ids["rule_a"], "document_id": ids["document_a"]})
    await session.execute(text("""
        INSERT INTO approved_fact_source (
            id, organization_id, property_id, subject_kind, subject_id, field_key,
            approved_value_json, value_fingerprint, valid_from, property_document_id,
            pdf_page, quote, source_import_id
        ) VALUES (
            :id, :org, :property_id, 'PROPERTY', :property_id, 'street',
            CAST(:value AS jsonb), :fingerprint, now() - interval '1 day', :document_id,
            1, 'Hauptstraße 1', :import_id
        )
    """), {**common, "id": ids["fact_source"], "document_id": ids["document_a"],
              "value": json.dumps("Hauptstraße 1"), "fingerprint": "c" * 64})
    return ids


@pytest.mark.asyncio
async def test_approval_tables_have_tenant_and_property_columns(migrator_session) -> None:
    result = await migrator_session.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(:tables)
              AND column_name IN ('organization_id', 'property_id')
            """
        ),
        {"tables": sorted(APPROVAL_TABLES)},
    )
    columns = {(row.table_name, row.column_name) for row in result}
    for table in APPROVAL_TABLES:
        assert (table, "organization_id") in columns
        assert (table, "property_id") in columns


@pytest.mark.asyncio
async def test_approval_tables_have_exact_app_rw_table_privileges(migrator_session) -> None:
    result = await migrator_session.execute(
        text(
            """
            SELECT table_name, privilege_type
            FROM information_schema.table_privileges
            WHERE table_schema = 'public'
              AND grantee = 'app_rw'
              AND table_name = ANY(:tables)
            """
        ),
        {"tables": sorted(APPROVAL_TABLES)},
    )
    grants: dict[str, set[str]] = {table: set() for table in APPROVAL_TABLES}
    for row in result:
        grants[row.table_name].add(row.privilege_type)
    for table, privileges in grants.items():
        assert {"SELECT", "INSERT"}.issubset(privileges), table
        assert not ({"UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"} & privileges), table


@pytest.mark.asyncio
async def test_approval_tables_have_exact_app_rw_update_columns(migrator_session) -> None:
    result = await migrator_session.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.column_privileges
            WHERE table_schema = 'public'
              AND grantee = 'app_rw'
              AND privilege_type = 'UPDATE'
              AND table_name = ANY(:tables)
            """
        ),
        {"tables": sorted(APPROVAL_TABLES)},
    )
    actual: dict[str, set[str]] = {table: set() for table in APPROVAL_TABLES}
    for row in result:
        actual[row.table_name].add(row.column_name)
    assert actual == UPDATE_COLUMNS


@pytest.mark.asyncio
async def test_0025_database_metadata_matches_declared_indexes_and_checks(migrator_session) -> None:
    for table, expected_indexes in EXPECTED_INDEXES.items():
        result = await migrator_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = :table"),
            {"table": table},
        )
        database_indexes = {row.indexname for row in result}
        assert expected_indexes <= database_indexes
        model_indexes = {index.name for index in Base.metadata.tables[table].indexes}
        assert expected_indexes == model_indexes
    checks = await migrator_session.execute(text("""
        SELECT conname FROM pg_constraint
        WHERE conrelid IN ('declaration_import'::regclass, 'declaration_candidate'::regclass)
          AND contype = 'c'
    """))
    names = {row.conname for row in checks}
    assert {"ck_declaration_import_approval_result_fields", "ck_declaration_candidate_corrected_value"} <= names
    assert "ck_declaration_import_approval_result_fields" in {
        constraint.name for constraint in Base.metadata.tables["declaration_import"].constraints
    }
    assert "ck_declaration_candidate_corrected_value" in {
        constraint.name for constraint in Base.metadata.tables["declaration_candidate"].constraints
    }
    checks = await migrator_session.execute(text("""
        SELECT tables.relname AS table_name, constraints.conname
        FROM pg_constraint AS constraints
        JOIN pg_class AS tables ON tables.oid = constraints.conrelid
        WHERE tables.relname = ANY(:tables)
          AND constraints.contype = 'c'
    """), {"tables": sorted(APPROVAL_TABLES)})
    database_checks: dict[str, set[str]] = {table: set() for table in APPROVAL_TABLES}
    for row in checks:
        database_checks[row.table_name].add(row.conname)
    assert database_checks == EXPECTED_CHECKS
    for table, expected_checks in EXPECTED_CHECKS.items():
        model_checks = {
            constraint.name
            for constraint in Base.metadata.tables[table].constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert model_checks == expected_checks


def test_0024_to_0025_backfills_legacy_applied_imports(seed_data) -> None:
    property_id = uuid.uuid4()
    applied_import_id = uuid.uuid4()
    draft_import_id = uuid.uuid4()
    corrected_candidate_id = uuid.uuid4()
    missing_value_candidate_id = uuid.uuid4()
    json_null_candidate_id = uuid.uuid4()
    legacy_corrected_value = {"kind": "TEXT", "value": "Talstraße 112"}
    post_0025_correction = {"kind": "TEXT", "value": "Kurfürstenstraße 7"}
    sync_url = os.environ["HV_DATABASE_URL_SYNC"]
    config = _declaration_alembic_config()
    database_at_head = True

    try:
        # This test already destroys later schemas. Clear synthetic source rows
        # before crossing the populated-origin rollback guard, not its trigger.
        from tests.conftest import clear_disposable_source_context
        with psycopg2.connect(sync_url) as connection, connection.cursor() as cursor:
            clear_disposable_source_context(cursor)
        command.downgrade(config, "0024")
        database_at_head = False
        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO property (
                        id, organization_id, name, street, zip_code, city, management_type
                    ) VALUES (%s, %s, 'Legacy approval property', '', '', '', 'WEG')
                    """,
                    (str(property_id), str(seed_data["org_a_id"])),
                )
                for import_id, status in (
                    (applied_import_id, "APPLIED"),
                    (draft_import_id, "DRAFT"),
                ):
                    cursor.execute(
                        """
                        INSERT INTO declaration_import (
                            id, organization_id, mode, target_property_id, status,
                            initiated_by, approved_by, approved_at, applied_property_id,
                            max_cost_usd
                        ) VALUES (
                            %s, %s, 'EXISTING_PROPERTY', %s, %s,
                            %s, %s, CASE WHEN %s = 'APPLIED' THEN now() END,
                            %s, 1
                        )
                        """,
                        (
                            str(import_id),
                            str(seed_data["org_a_id"]),
                            str(property_id),
                            status,
                            str(seed_data["user_a_admin_id"]),
                            str(seed_data["user_a_admin_id"]) if status == "APPLIED" else None,
                            status,
                            str(property_id) if status == "APPLIED" else None,
                        ),
                    )
                cursor.execute(
                    """
                    INSERT INTO declaration_candidate (
                        id, organization_id, import_id, stable_key, candidate_type,
                        subject_key, field_key, operation, proposed_value_json,
                        confidence, risk_class, requires_individual_review,
                        review_status, reviewed_by, reviewed_at
                    ) VALUES (
                        %s, %s, %s, 'legacy-corrected-street', 'PROPERTY_FIELD',
                        'property', 'street', 'UPDATE', %s::jsonb,
                        0.95, 'LOW', false,
                        'CORRECTED', %s, now()
                    )
                    """,
                    (
                        str(corrected_candidate_id),
                        str(seed_data["org_a_id"]),
                        str(applied_import_id),
                        json.dumps(legacy_corrected_value),
                        str(seed_data["user_a_admin_id"]),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO declaration_candidate (
                        id, organization_id, import_id, stable_key, candidate_type,
                        subject_key, field_key, operation, proposed_value_json,
                        confidence, risk_class, requires_individual_review,
                        review_status, reviewed_by, reviewed_at
                    ) VALUES (
                        %s, %s, %s, 'legacy-corrected-json-null', 'PROPERTY_FIELD',
                        'property', 'zip_code', 'UPDATE', 'null'::jsonb,
                        0.95, 'LOW', false,
                        'CORRECTED', %s, now()
                    )
                    """,
                    (
                        str(json_null_candidate_id),
                        str(seed_data["org_a_id"]),
                        str(applied_import_id),
                        str(seed_data["user_a_admin_id"]),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO declaration_candidate (
                        id, organization_id, import_id, stable_key, candidate_type,
                        subject_key, field_key, operation, proposed_value_json,
                        confidence, risk_class, requires_individual_review,
                        review_status, reviewed_by, reviewed_at
                    ) VALUES (
                        %s, %s, %s, 'legacy-corrected-without-value', 'PROPERTY_FIELD',
                        'property', 'city', 'UPDATE', NULL,
                        0.95, 'LOW', false,
                        'CORRECTED', %s, now()
                    )
                    """,
                    (
                        str(missing_value_candidate_id),
                        str(seed_data["org_a_id"]),
                        str(applied_import_id),
                        str(seed_data["user_a_admin_id"]),
                    ),
                )

        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, applied_property_id FROM declaration_import WHERE id = %s",
                    (str(applied_import_id),),
                )
                assert cursor.fetchone() == ("APPLIED", str(property_id))

        # Rehearse only the historical migration under test; later populated
        # security boundaries deliberately reject this backward round-trip.
        command.upgrade(config, "0025")
        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status, applied_property_id, approval_idempotency_key,
                           approval_result_json
                    FROM declaration_import
                    WHERE id IN (%s, %s)
                    ORDER BY id = %s DESC
                    """,
                    (str(applied_import_id), str(draft_import_id), str(applied_import_id)),
                )
                applied_row, draft_row = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT review_status, proposed_value_json, corrected_value_json
                    FROM declaration_candidate
                    WHERE id = %s
                    """,
                    (str(corrected_candidate_id),),
                )
                corrected_candidate_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT review_status, proposed_value_json, corrected_value_json
                    FROM declaration_candidate
                    WHERE id = %s
                    """,
                    (str(missing_value_candidate_id),),
                )
                missing_value_candidate_row = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT review_status, proposed_value_json IS NULL,
                           jsonb_typeof(proposed_value_json),
                           corrected_value_json IS NULL
                    FROM declaration_candidate
                    WHERE id = %s
                    """,
                    (str(json_null_candidate_id),),
                )
                json_null_candidate_row = cursor.fetchone()

        assert applied_row[:3] == (
            "APPLIED",
            str(property_id),
            f"legacy-0025-{applied_import_id}",
        )
        assert applied_row[3] == {
            "legacy_migration": "0025_declaration_approval_backfill",
            "applied_property_id": str(property_id),
        }
        assert draft_row[0] == "DRAFT"
        assert draft_row[1:] == (None, None, None)
        assert corrected_candidate_row == (
            "CORRECTED",
            legacy_corrected_value,
            legacy_corrected_value,
        )
        assert missing_value_candidate_row == (
            "NEEDS_CLARIFICATION",
            None,
            None,
        )
        assert json_null_candidate_row == (
            "NEEDS_CLARIFICATION",
            False,
            "null",
            True,
        )

        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE declaration_candidate
                    SET corrected_value_json = %s::jsonb
                    WHERE id = %s
                    """,
                    (json.dumps(post_0025_correction), str(corrected_candidate_id)),
                )
                cursor.execute(
                    """
                    SELECT proposed_value_json, corrected_value_json
                    FROM declaration_candidate
                    WHERE id = %s
                    """,
                    (str(corrected_candidate_id),),
                )
                pre_downgrade_candidate_values = cursor.fetchone()

        assert pre_downgrade_candidate_values == (
            legacy_corrected_value,
            post_0025_correction,
        )

        command.downgrade(config, "0024")
        database_at_head = False
        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT review_status, proposed_value_json
                    FROM declaration_candidate
                    WHERE id = %s
                    """,
                    (str(corrected_candidate_id),),
                )
                downgraded_candidate_row = cursor.fetchone()

        assert downgraded_candidate_row == (
            "NEEDS_CLARIFICATION",
            legacy_corrected_value,
        )

        command.upgrade(config, "head")
        database_at_head = True
        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT review_status, proposed_value_json, corrected_value_json
                    FROM declaration_candidate
                    WHERE id = %s
                    """,
                    (str(corrected_candidate_id),),
                )
                reupgraded_candidate_row = cursor.fetchone()

        assert reupgraded_candidate_row == (
            "NEEDS_CLARIFICATION",
            legacy_corrected_value,
            None,
        )
    finally:
        if not database_at_head:
            command.upgrade(config, "head")
        with psycopg2.connect(sync_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM declaration_candidate WHERE id IN (%s, %s, %s)",
                    (
                        str(corrected_candidate_id),
                        str(missing_value_candidate_id),
                        str(json_null_candidate_id),
                    ),
                )
                cursor.execute(
                    "DELETE FROM declaration_import WHERE id IN (%s, %s)",
                    (str(applied_import_id), str(draft_import_id)),
                )
                cursor.execute("DELETE FROM property WHERE id = %s", (str(property_id),))


@pytest.mark.asyncio
async def test_approval_graph_is_tenant_isolated_and_composite_edges_are_enforced(app_rw_session, seed_data) -> None:
    await _set_org(app_rw_session, seed_data["org_a_id"])
    ids = await _insert_approval_graph(app_rw_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    await _set_org(app_rw_session, seed_data["org_b_id"])
    for table in APPROVAL_TABLES:
        result = await app_rw_session.execute(text(f'SELECT count(*) FROM "{table}"'))
        assert result.scalar_one() == 0
    # RLS is distinct from a missing INSERT grant: an org B context cannot claim org A.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""
                INSERT INTO property_legal_reference (organization_id, property_id, reference_type, details_json, source_import_id)
                VALUES (:org, :property_id, 'LAND_REGISTER', '{}'::jsonb, :import_id)
            """), {"org": seed_data["org_a_id"], "property_id": ids["property"], "import_id": ids["import"]})

    # A property-scoped composite FK rejects a same-org reference to a foreign-property unit.
    foreign_property = uuid.uuid4()
    foreign_unit = uuid.uuid4()
    await app_rw_session.execute(text("""
        INSERT INTO property (id, organization_id, name, management_type)
        VALUES (:property_id, :org, 'Foreign property', 'WEG')
    """), {"property_id": foreign_property, "org": seed_data["org_b_id"]})
    await app_rw_session.execute(text("""
        INSERT INTO unit (id, organization_id, property_id, number, usage_type)
        VALUES (:id, :org, :property_id, '2', 'WOHNUNG')
    """), {"id": foreign_unit, "org": seed_data["org_b_id"], "property_id": foreign_property})
    await _set_org(app_rw_session, seed_data["org_a_id"])
    with pytest.raises(DBAPIError, match="foreign key"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""
                INSERT INTO property_assignment (organization_id, property_id, unit_id, stable_key, assignment_type, designation, details_json, source_import_id)
                VALUES (:org, :property_id, :unit_id, 'cross-property', 'SPECIAL_USE', 'Nope', '{}'::jsonb, :import_id)
            """), {"org": seed_data["org_a_id"], "property_id": ids["property"], "unit_id": foreign_unit, "import_id": ids["import"]})


@pytest.mark.asyncio
async def test_approval_lifecycle_grants_allow_only_lifecycle_fields(app_rw_session, seed_data) -> None:
    await _set_org(app_rw_session, seed_data["org_a_id"])
    ids = await _insert_approval_graph(app_rw_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    # Each granted lifecycle/projection update is usable by app_rw.
    await app_rw_session.execute(text("UPDATE property_legal_reference SET updated_at=now() WHERE id=:id"), {"id": ids["legal"]})
    await app_rw_session.execute(text("UPDATE property_legal_reference SET status='SUPERSEDED', valid_to=now() WHERE id=:id"), {"id": ids["legal"]})
    await app_rw_session.execute(text("UPDATE property_assignment SET updated_at=now() WHERE id=:id"), {"id": ids["assignment"]})
    await app_rw_session.execute(text("UPDATE property_assignment SET status='SUPERSEDED', valid_to=now() WHERE id=:id"), {"id": ids["assignment"]})
    await app_rw_session.execute(text("UPDATE property_document_relation SET updated_at=now() WHERE id=:id"), {"id": ids["document_relation"]})
    await app_rw_session.execute(text("UPDATE property_document_relation SET status='SUPERSEDED' WHERE id=:id"), {"id": ids["document_relation"]})
    await app_rw_session.execute(text("UPDATE property_rule SET search_text=:text, updated_at=now(), valid_to=now(), status='NEEDS_CLARIFICATION' WHERE id=:id"), {"id": ids["rule_a"], "text": "x" * 100000})
    await app_rw_session.execute(text("UPDATE approved_fact_source SET updated_at=now() WHERE id=:id"), {"id": ids["fact_source"]})
    await app_rw_session.execute(text("UPDATE approved_fact_source SET status='SUPERSEDED', valid_to=now() WHERE id=:id"), {"id": ids["fact_source"]})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("UPDATE property_rule SET title='Mutable?' WHERE id=:id"), {"id": ids["rule_b"]})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("DELETE FROM property_rule_source WHERE id=:id"), {"id": ids["rule_source"]})
    with pytest.raises(DBAPIError, match="100000"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("UPDATE property_rule SET search_text=:text WHERE id=:id"), {"id": ids["rule_b"], "text": "x" * 100001})


@pytest.mark.asyncio
async def test_applied_import_requires_the_complete_idempotency_result_pair(app_rw_session, seed_data) -> None:
    await _set_org(app_rw_session, seed_data["org_a_id"])
    ids = await _insert_approval_graph(app_rw_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    with pytest.raises(DBAPIError, match="approval_result_fields"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""
                UPDATE declaration_import
                SET status = 'APPLIED', approved_by = :user_id, approved_at = now(),
                    applied_property_id = :property_id
                WHERE id = :import_id
            """), {"user_id": seed_data["user_a_admin_id"], "property_id": ids["property"], "import_id": ids["import"]})
    with pytest.raises(DBAPIError, match="approval_result_fields"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(text("""
                UPDATE declaration_import
                SET approval_idempotency_key = 'approval-result-test-0001'
                WHERE id = :import_id
            """), {"import_id": ids["import"]})


@pytest.mark.asyncio
async def test_migrator_triggers_freeze_omitted_authoritative_fields_and_enforce_lifecycles(migrator_session, seed_data) -> None:
    ids = await _insert_approval_graph(migrator_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    # app_migrator bypasses grants: these failures prove the triggers, not ACLs.
    for column, expression in (("id", "gen_random_uuid()"), ("created_at", "created_at + interval '1 second'"), ("valid_from", "valid_from + interval '1 second'"), ("quote", "'rewritten'")):
        with pytest.raises(DBAPIError):
            async with migrator_session.begin_nested():
                await migrator_session.execute(text(f"UPDATE approved_fact_source SET {column} = {expression} WHERE id = :id"), {"id": ids["fact_source"]})
    await migrator_session.execute(text("UPDATE approved_fact_source SET updated_at = now() WHERE id = :id"), {"id": ids["fact_source"]})
    await migrator_session.execute(text("UPDATE approved_fact_source SET status='SUPERSEDED', valid_to=now() WHERE id = :id"), {"id": ids["fact_source"]})
    with pytest.raises(DBAPIError):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE approved_fact_source SET status='EFFECTIVE', valid_to=NULL WHERE id = :id"), {"id": ids["fact_source"]})
    for column, expression in (("id", "gen_random_uuid()"), ("created_at", "created_at + interval '1 second'"), ("valid_from", "now()"), ("title", "'rewritten'")):
        with pytest.raises(DBAPIError):
            async with migrator_session.begin_nested():
                await migrator_session.execute(text(f"UPDATE property_rule SET {column} = {expression} WHERE id = :id"), {"id": ids["rule_a"]})
    await migrator_session.execute(text("UPDATE property_rule SET updated_at=now(), search_text='projection', valid_to=now() WHERE id=:id"), {"id": ids["rule_a"]})
    await migrator_session.execute(text("UPDATE property_rule SET status='NEEDS_CLARIFICATION' WHERE id=:id"), {"id": ids["rule_a"]})
    await migrator_session.execute(text("UPDATE property_rule SET status='EFFECTIVE' WHERE id=:id"), {"id": ids["rule_a"]})
    await migrator_session.execute(text("UPDATE property_rule SET status='SUPERSEDED' WHERE id=:id"), {"id": ids["rule_a"]})
    with pytest.raises(DBAPIError):
        async with migrator_session.begin_nested():
            await migrator_session.execute(text("UPDATE property_rule SET status='EFFECTIVE' WHERE id=:id"), {"id": ids["rule_a"]})
    await migrator_session.execute(text("UPDATE property_rule SET status='RETRACTED' WHERE id=:id"), {"id": ids["rule_b"]})


@pytest.mark.asyncio
async def test_every_approval_table_rejects_cross_tenant_insert_delete_and_immutable_update(app_rw_session, seed_data) -> None:
    await _set_org(app_rw_session, seed_data["org_a_id"])
    ids = await _insert_approval_graph(app_rw_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    inserts = {
        "property_legal_reference": "(organization_id, property_id, reference_type, details_json, source_import_id) VALUES (:org, :property, 'LAND_REGISTER', '{}'::jsonb, :imp)",
        "property_assignment": "(organization_id, property_id, stable_key, assignment_type, designation, details_json, source_import_id) VALUES (:org, :property, 'rls-assignment', 'SPECIAL_USE', 'RLS', '{}'::jsonb, :imp)",
        "property_document_relation": "(organization_id, property_id, source_document_id, target_document_id, relation_type, source_import_id) VALUES (:org, :property, :doc_b, :doc_a, 'AMENDS', :imp)",
        "property_rule": "(organization_id, property_id, stable_key, topic, title, summary, scope_json, effect_json, source_import_id, approved_by, approved_at) VALUES (:org, :property, 'rls-rule', 'topic', 'RLS', 'RLS', '{}'::jsonb, '{}'::jsonb, :imp, :user_id, now())",
        "property_rule_relation": "(organization_id, property_id, source_rule_id, target_rule_id, relation_type, source_import_id) VALUES (:org, :property, :rule_b, :rule_a, 'SUPERSEDES', :imp)",
        "property_rule_source": "(organization_id, property_id, property_rule_id, property_document_id, pdf_page, quote) VALUES (:org, :property, :rule_a, :doc_a, 9, 'RLS')",
        "approved_fact_source": "(organization_id, property_id, subject_kind, subject_id, field_key, approved_value_json, value_fingerprint, valid_from, property_document_id, pdf_page, quote, source_import_id) VALUES (:org, :property, 'PROPERTY', :property, 'rls', '{}'::jsonb, 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd', now(), :doc_a, 9, 'RLS', :imp)",
    }
    row_ids = {
        "property_legal_reference": ids["legal"], "property_assignment": ids["assignment"],
        "property_document_relation": ids["document_relation"], "property_rule": ids["rule_a"],
        "property_rule_relation": ids["rule_relation"], "property_rule_source": ids["rule_source"],
        "approved_fact_source": ids["fact_source"],
    }
    immutable_columns = {
        "property_legal_reference": "reference_type='MUTABLE'", "property_assignment": "designation='MUTABLE'",
        "property_document_relation": "relation_type='REFERENCES'", "property_rule": "title='MUTABLE'",
        "property_rule_relation": "relation_type='CONFLICTS'", "property_rule_source": "quote='MUTABLE'",
        "approved_fact_source": "quote='MUTABLE'",
    }
    params = {"org": seed_data["org_a_id"], "property": ids["property"], "imp": ids["import"], "doc_a": ids["document_a"], "doc_b": ids["document_b"], "rule_a": ids["rule_a"], "rule_b": ids["rule_b"], "user_id": seed_data["user_a_admin_id"]}
    await _set_org(app_rw_session, seed_data["org_b_id"])
    for table, values in inserts.items():
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(f"INSERT INTO {table} {values}"), params)
    await _set_org(app_rw_session, seed_data["org_a_id"])
    for table, row_id in row_ids.items():
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": row_id})
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(f"UPDATE {table} SET {immutable_columns[table]} WHERE id=:id"), {"id": row_id})


@pytest.mark.asyncio
async def test_all_0025_property_scoped_child_edges_reject_cross_property_and_cross_tenant_rows(migrator_session, seed_data) -> None:
    primary = await _insert_approval_graph(migrator_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    same_org_other_property = await _insert_approval_graph(migrator_session, seed_data["org_a_id"], seed_data["user_a_admin_id"])
    foreign = await _insert_approval_graph(migrator_session, seed_data["org_b_id"], seed_data["user_b_admin_id"])

    async def rejects_fk(statement: str, values: dict) -> None:
        with pytest.raises(DBAPIError, match="foreign key"):
            async with migrator_session.begin_nested():
                await migrator_session.execute(text(statement), values)

    common = {"org": seed_data["org_a_id"], "property": primary["property"], "imp": primary["import"], "user": seed_data["user_a_admin_id"], "doc_a": primary["document_a"], "doc_b": primary["document_b"], "rule_a": primary["rule_a"], "rule_b": primary["rule_b"]}
    await rejects_fk("""INSERT INTO property_assignment (organization_id, property_id, unit_id, stable_key, assignment_type, designation, details_json, source_import_id) VALUES (:org,:property,:unit,'cross-property-unit','SPECIAL_USE','x','{}'::jsonb,:imp)""", {**common, "unit": same_org_other_property["unit"]})
    await rejects_fk("""INSERT INTO property_document_relation (organization_id, property_id, source_document_id, target_document_id, relation_type, source_import_id) VALUES (:org,:property,:source,:target,'AMENDS',:imp)""", {**common, "source": same_org_other_property["document_a"], "target": primary["document_b"]})
    await rejects_fk("""INSERT INTO property_document_relation (organization_id, property_id, source_document_id, target_document_id, relation_type, source_import_id) VALUES (:org,:property,:source,:target,'SUPPLEMENTS',:imp)""", {**common, "source": primary["document_a"], "target": same_org_other_property["document_b"]})
    await rejects_fk("""INSERT INTO property_document_relation (organization_id, property_id, source_document_id, target_document_id, relation_type, source_import_id) VALUES (:org,:property,:source,:target,'REFERENCES',:imp)""", {**common, "source": primary["document_a"], "target": primary["document_b"], "imp": foreign["import"]})
    await rejects_fk("""INSERT INTO property_legal_reference (organization_id, property_id, reference_type, details_json, source_import_id) VALUES (:org,:property,'LAND_REGISTER','{}'::jsonb,:imp)""", {**common, "imp": foreign["import"]})
    await rejects_fk("""INSERT INTO property_rule (organization_id, property_id, stable_key, topic, title, summary, scope_json, effect_json, source_import_id, approved_by, approved_at) VALUES (:org,:property,'foreign-import','t','t','s','{}'::jsonb,'{}'::jsonb,:imp,:user,now())""", {**common, "imp": foreign["import"]})
    await rejects_fk("""INSERT INTO property_rule (organization_id, property_id, stable_key, topic, title, summary, scope_json, effect_json, source_import_id, approved_by, approved_at) VALUES (:org,:property,'foreign-user','t','t','s','{}'::jsonb,'{}'::jsonb,:imp,:user,now())""", {**common, "user": seed_data["user_b_admin_id"]})
    await rejects_fk("""INSERT INTO property_rule_relation (organization_id, property_id, source_rule_id, target_rule_id, relation_type, source_import_id) VALUES (:org,:property,:source,:target,'SUPERSEDES',:imp)""", {**common, "source": same_org_other_property["rule_a"], "target": primary["rule_b"]})
    await rejects_fk("""INSERT INTO property_rule_relation (organization_id, property_id, source_rule_id, target_rule_id, relation_type, source_import_id) VALUES (:org,:property,:source,:target,'SUPPLEMENTS',:imp)""", {**common, "source": primary["rule_a"], "target": same_org_other_property["rule_b"]})
    await rejects_fk("""INSERT INTO property_rule_relation (organization_id, property_id, source_rule_id, target_rule_id, relation_type, source_import_id) VALUES (:org,:property,:source,:target,'CONFLICTS',:imp)""", {**common, "source": primary["rule_a"], "target": primary["rule_b"], "imp": foreign["import"]})
    await rejects_fk("""INSERT INTO property_rule_source (organization_id, property_id, property_rule_id, property_document_id, pdf_page, quote) VALUES (:org,:property,:rule,:document,12,'x')""", {**common, "rule": same_org_other_property["rule_a"], "document": primary["document_a"]})
    await rejects_fk("""INSERT INTO property_rule_source (organization_id, property_id, property_rule_id, property_document_id, pdf_page, quote) VALUES (:org,:property,:rule,:document,13,'x')""", {**common, "rule": primary["rule_a"], "document": same_org_other_property["document_a"]})
    await rejects_fk("""INSERT INTO approved_fact_source (organization_id, property_id, subject_kind, subject_id, field_key, approved_value_json, value_fingerprint, valid_from, property_document_id, pdf_page, quote, source_import_id) VALUES (:org,:property,'PROPERTY',:property,'document','{}'::jsonb,'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',now(),:document,12,'x',:imp)""", {**common, "document": same_org_other_property["document_a"]})
    await rejects_fk("""INSERT INTO approved_fact_source (organization_id, property_id, subject_kind, subject_id, field_key, approved_value_json, value_fingerprint, valid_from, property_document_id, pdf_page, quote, source_import_id) VALUES (:org,:property,'PROPERTY',:property,'import','{}'::jsonb,'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',now(),:document,13,'x',:imp)""", {**common, "document": primary["document_a"], "imp": foreign["import"]})


async def _set_tenant_context(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :organization_id, false)"),
        {"organization_id": str(organization_id)},
    )


def _text(value: str) -> dict[str, object]:
    return {"kind": "TEXT", "value": value}


def _candidate(
    item: DeclarationImport,
    *,
    stable_key: str,
    candidate_type: str,
    subject_key: str,
    value: dict[str, object],
    field_key: str | None = None,
) -> DeclarationCandidate:
    return DeclarationCandidate(
        organization_id=item.organization_id,
        import_id=item.id,
        stable_key=stable_key,
        candidate_type=candidate_type,
        subject_key=subject_key,
        field_key=field_key,
        operation="CREATE",
        proposed_value_json=value,
        confidence=Decimal("0.99"),
        risk_class="LOW",
        requires_individual_review=False,
        review_status="ACCEPTED",
        reviewed_by=item.initiated_by,
        reviewed_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def fully_reviewed_new_property_import(seed_data: dict) -> SimpleNamespace:
    """A committed, complete NEW_PROPERTY graph with FK-safe teardown."""
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    item: DeclarationImport | None = None
    main: PropertyDocument | None = None
    amendment: PropertyDocument | None = None
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            item = DeclarationImport(
                organization_id=seed_data["org_a_id"],
                mode="NEW_PROPERTY",
                status="NEEDS_REVIEW",
                draft_revision=12,
                initiated_by=seed_data["user_a_admin_id"],
                max_cost_usd=Decimal("10"),
            )
            main = PropertyDocument(
                organization_id=item.organization_id,
                doc_type="DECLARATION",
                title="Teilungserklärung",
                original_filename="teilung.pdf",
                mime_type="application/pdf",
                content_byte_size=10,
                content_sha256="a" * 64,
                storage_uri="test/approval-main.pdf",
                storage_backend="LOCAL",
                page_count=3,
                uploaded_by=seed_data["user_a_admin_id"],
            )
            amendment = PropertyDocument(
                organization_id=item.organization_id,
                doc_type="AMENDMENT",
                title="Nachtrag",
                original_filename="nachtrag.pdf",
                mime_type="application/pdf",
                content_byte_size=10,
                content_sha256="b" * 64,
                storage_uri="test/approval-amendment.pdf",
                storage_backend="LOCAL",
                page_count=2,
                uploaded_by=seed_data["user_a_admin_id"],
            )
            session.add_all([item, main, amendment])
            await session.flush()
            session.add_all([
                DeclarationImportDocument(
                    organization_id=item.organization_id,
                    import_id=item.id,
                    document_id=main.id,
                    sequence_no=1,
                    document_role="DECLARATION",
                ),
                DeclarationImportDocument(
                    organization_id=item.organization_id,
                    import_id=item.id,
                    document_id=amendment.id,
                    sequence_no=2,
                    document_role="AMENDMENT",
                ),
            ])
            candidates = [
                _candidate(item, stable_key="property:name", candidate_type="PROPERTY_FIELD", subject_key="property:new", field_key="name", value=_text("WEG Talstraße 111")),
                _candidate(item, stable_key="property:street", candidate_type="PROPERTY_FIELD", subject_key="property:new", field_key="street", value=_text("Talstraße 111")),
                _candidate(item, stable_key="property:zip", candidate_type="PROPERTY_FIELD", subject_key="property:new", field_key="zip_code", value=_text("40217")),
                _candidate(item, stable_key="property:city", candidate_type="PROPERTY_FIELD", subject_key="property:new", field_key="city", value=_text("Düsseldorf")),
                _candidate(item, stable_key="property:type", candidate_type="PROPERTY_FIELD", subject_key="property:new", field_key="management_type", value=_text("WEG")),
                _candidate(item, stable_key="property:mea-denominator", candidate_type="PROPERTY_FIELD", subject_key="property:new", field_key="mea_denominator", value={"kind": "INTEGER", "value": 1_000_000}),
                _candidate(item, stable_key="unit-create:unit:1", candidate_type="UNIT_CREATE", subject_key="unit:1", value={"number": _text("1"), "usage_type": _text("WOHNUNG"), "location": _text("EG links"), "mea": {"kind": "MEA", "numerator": 500_000, "denominator": 1_000_000}}),
                _candidate(item, stable_key="unit-create:unit:2", candidate_type="UNIT_CREATE", subject_key="unit:2", value={"number": _text("2"), "usage_type": _text("WOHNUNG"), "building_part": _text("Haus A"), "mea": {"kind": "MEA", "numerator": 500_000, "denominator": 1_000_000}}),
                _candidate(item, stable_key="legal:1", candidate_type="LEGAL_REFERENCE", subject_key="legal_reference:1", value={"reference_type": _text("LAND_REGISTER"), "district": _text("Düsseldorf"), "sheet": _text("1234")}),
                _candidate(item, stable_key="assignment:parking", candidate_type="ASSIGNMENT", subject_key="assignment:parking", value={"assignment_type": _text("SPECIAL_USE"), "designation": _text("Stellplatz 2"), "unit_subject_key": _text("unit:2")}),
                _candidate(item, stable_key="rule:heating", candidate_type="RULE", subject_key="rule:heating", value={"title": "Heizkosten", "topic": "heating_costs", "summary": "Verteilung nach Verbrauch und Fläche.", "scope": {"all_units": True}, "effect": {"allocation_formula": {"declared_complete": True, "parts": [{"label": "Verbrauch", "basis": "Verbrauch", "share": {"kind": "PERCENTAGE", "value": "70"}}, {"label": "Fläche", "basis": "Fläche", "share": {"kind": "PERCENTAGE", "value": "30"}}]}}, "proposed_status": "EFFECTIVE", "supersedes_subject_keys": [], "conflicts_with_subject_keys": [], "clarification_needed": False}),
                _candidate(item, stable_key="relation:amends", candidate_type="DOCUMENT_RELATION", subject_key=str(amendment.id), value={"relation_type": "AMENDS", "target_document_key": str(main.id)}),
            ]
            session.add_all(candidates)
            await session.flush()
            for candidate in candidates:
                source = amendment if candidate.candidate_type in {"RULE", "DOCUMENT_RELATION"} else main
                session.add(DeclarationCandidateEvidence(
                    organization_id=item.organization_id,
                    candidate_id=candidate.id,
                    document_id=source.id,
                    pdf_page=1,
                    printed_page="1",
                    section_ref="§ 1",
                    quote="Belegstelle der Teilungserklärung",
                    field_path=candidate.field_key,
                ))
            # The heating rule carries two independently traceable sources.
            heating = next(candidate for candidate in candidates if candidate.stable_key == "rule:heating")
            session.add(DeclarationCandidateEvidence(
                organization_id=item.organization_id,
                candidate_id=heating.id,
                document_id=main.id,
                pdf_page=2,
                printed_page="2",
                section_ref="§ 8",
                quote="Heizkosten werden verteilt.",
                field_path="effect.allocation_formula",
            ))
            await session.commit()
            yield SimpleNamespace(item=item, main=main, amendment=amendment)
    finally:
        await engine.dispose()
        if item is not None and main is not None and amendment is not None:
            cleanup_engine = create_async_engine(settings.database_url_migrator)
            try:
                factory = async_sessionmaker(cleanup_engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as cleanup:
                    async with cleanup.begin():
                        from tests.conftest import fixture_has_registered_documents

                        registered = await fixture_has_registered_documents(cleanup, [main.id, amendment.id])
                        published = await cleanup.scalar(text("SELECT publication_domain_id FROM declaration_import WHERE id=:id"), {"id": item.id})
                        # Published/registered originals remain until the guarded session reset.
                        if not registered and published is None:
                            from app.declarations.approval import deterministic_id
                            property_id = deterministic_id(item.organization_id, item.id, "property", "new")
                            await cleanup.execute(text("DELETE FROM approved_fact_source WHERE source_import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_rule_source WHERE property_rule_id IN (SELECT id FROM property_rule WHERE source_import_id = :import_id)"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_rule_relation WHERE source_import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_rule WHERE source_import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_document_relation WHERE source_import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_assignment WHERE source_import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_legal_reference WHERE source_import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM audit_event WHERE entity_id = :import_id AND action = 'declaration_import.applied'"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM declaration_candidate_evidence WHERE candidate_id IN (SELECT id FROM declaration_candidate WHERE import_id = :import_id)"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM declaration_candidate_issue WHERE import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM declaration_candidate WHERE import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM declaration_import_document WHERE import_id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM declaration_import WHERE id = :import_id"), {"import_id": item.id})
                            await cleanup.execute(text("DELETE FROM property_document WHERE id IN (:main_id, :amendment_id)"), {"main_id": main.id, "amendment_id": amendment.id})
                            await cleanup.execute(text("DELETE FROM unit WHERE property_id = :property_id"), {"property_id": property_id})
                            await cleanup.execute(text("DELETE FROM property WHERE id = :property_id"), {"property_id": property_id})
            finally:
                await cleanup_engine.dispose()


@pytest.mark.asyncio
async def test_new_property_approval_is_atomic_and_traceable(
    client_a_clerk,
    fully_reviewed_new_property_import,
    seed_data,
) -> None:
    item = fully_reviewed_new_property_import.item
    response = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "new-property-approval-0001"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "APPLIED"
    assert result["summary"] == {
        "properties_created": 1,
        "units_created": 2,
        "units_updated": 0,
        "rules_created": 1,
        "documents_bound": 2,
    }
    property_id = uuid.UUID(result["property_id"])

    from app.core.config import settings
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            property_row = await session.scalar(select(Property).where(Property.id == property_id))
            units = (await session.scalars(select(Unit).where(Unit.property_id == property_id).order_by(Unit.number))).all()
            assignment = await session.scalar(select(PropertyAssignment).where(PropertyAssignment.property_id == property_id))
            rule = await session.scalar(select(PropertyRule).where(PropertyRule.property_id == property_id))
            sources = (await session.scalars(select(PropertyRuleSource).where(PropertyRuleSource.property_id == property_id))).all()
            relation = await session.scalar(select(PropertyDocumentRelation).where(PropertyDocumentRelation.property_id == property_id))
            facts = (await session.scalars(select(ApprovedFactSource).where(ApprovedFactSource.property_id == property_id))).all()
            legal = await session.scalar(select(PropertyLegalReference).where(PropertyLegalReference.property_id == property_id))
            audit = await session.scalar(select(AuditEvent).where(AuditEvent.entity_id == item.id, AuditEvent.action == "declaration_import.applied"))
            document_rows = await session.scalars(
                select(PropertyDocument).where(
                    PropertyDocument.id.in_(
                        [
                            fully_reviewed_new_property_import.main.id,
                            fully_reviewed_new_property_import.amendment.id,
                        ]
                    )
                )
            )
            documents = document_rows.all()
            assert property_row is not None
            assert property_row.mea_denominator == 1_000_000
            assert property_row.mea_scale_status == "CONFIRMED"
            assert [unit.mea for unit in units] == [Decimal("500000"), Decimal("500000")]
            assert assignment is not None and assignment.unit_id == units[1].id
            assert legal is not None and legal.reference_type == "LAND_REGISTER"
            assert rule is not None and rule.status == "EFFECTIVE"
            assert len(sources) == 2
            assert relation is not None and relation.relation_type == "AMENDS"
            assert facts and all(source.source_import_id == item.id for source in facts)
            assert audit is not None
            assert {document.property_id for document in documents} == {property_id}
            assert audit.payload_diff == {"import_id": str(item.id), "property_id": str(property_id), "summary": result["summary"]}
            subject_ids = {property_id, assignment.id, legal.id, relation.id, rule.id}
            subject_ids.update(unit.id for unit in units)
            assert all(source.subject_id in subject_ids for source in facts)
    finally:
        await engine.dispose()

    replay = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": 0, "idempotency_key": "new-property-approval-0001"},
    )
    assert replay.status_code == 409
    replay = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "new-property-approval-0001"},
    )
    assert replay.status_code == 200
    assert replay.json() == result
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            assert await session.scalar(select(func.count()).select_from(Property).where(Property.id == property_id)) == 1
            assert await session.scalar(select(func.count()).select_from(Unit).where(Unit.property_id == property_id)) == 2
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == item.id, AuditEvent.action == "declaration_import.applied")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approved_additional_management_fact_from_pipeline_persists_as_rule(
    client_a_clerk,
    fully_reviewed_new_property_import,
    seed_data,
) -> None:
    item = fully_reviewed_new_property_import.item
    generated, _issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{
            "document_key": "source",
            "document_type": "DECLARATION",
            "title": "Teilungserklärung",
        }]),
        ReconciliationOutput(
            facts=[],
            rules=[],
            additional_facts=[AdditionalFactPayload(
                subject_key="additional:heating-cost-allocation",
                label="Heizkostenverteilung",
                value="Die Heizkosten werden nach Verbrauch und Wohnfläche verteilt.",
                management_relevance="Bei Jahresabrechnungen und Wirtschaftsplänen berücksichtigen.",
                evidence=[{
                    "document_key": "source",
                    "pdf_page": 1,
                    "quote": "Heizkosten nach Verbrauch und Fläche.",
                }],
                confidence=0.91,
                clarification_needed=False,
            )],
        ),
        (),
        {"source": fully_reviewed_new_property_import.main.id},
        None,
        (),
    )
    generated_candidate = next(
        candidate
        for candidate in generated
        if candidate.stable_key == "additional-rule:additional:heating-cost-allocation"
    )

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            candidate = DeclarationCandidate(
                organization_id=item.organization_id,
                import_id=item.id,
                stable_key=generated_candidate.stable_key,
                candidate_type=generated_candidate.candidate_type,
                subject_key=generated_candidate.subject_key,
                field_key=generated_candidate.field_key,
                operation=generated_candidate.operation,
                proposed_value_json=generated_candidate.proposed_value_json,
                confidence=generated_candidate.confidence,
                risk_class=generated_candidate.risk_class,
                requires_individual_review=generated_candidate.requires_individual_review,
                review_status="ACCEPTED",
                reviewed_by=item.initiated_by,
                reviewed_at=datetime.now(timezone.utc),
            )
            session.add(candidate)
            await session.flush()
            session.add(DeclarationCandidateEvidence(
                organization_id=item.organization_id,
                candidate_id=candidate.id,
                document_id=fully_reviewed_new_property_import.main.id,
                pdf_page=1,
                printed_page="1",
                section_ref="§ 7",
                quote="Heizkosten nach Verbrauch und Fläche.",
            ))
            await session.commit()

        response = await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={"draft_revision": item.draft_revision, "idempotency_key": "additional-fact-approval-0001"},
        )
        assert response.status_code == 200
        assert response.json()["summary"]["rules_created"] == 2
        property_id = uuid.UUID(response.json()["property_id"])
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            rule = await session.scalar(
                select(PropertyRule).where(
                    PropertyRule.property_id == property_id,
                    PropertyRule.source_import_id == item.id,
                    PropertyRule.title == "Heizkostenverteilung",
                )
            )
            assert rule is not None
            assert rule.scope_json == {"all_units": False, "unit_subject_keys": [], "building_parts": [], "participant_groups": [], "additional_scope": ["additional:heating-cost-allocation"]}
            assert rule.effect_json == {"obligation": None, "entitlement": None, "responsibility": [], "allocation_formula": None, "exceptions": [], "consequence": None, "administrative_note": "Bei Jahresabrechnungen und Wirtschaftsplänen berücksichtigen."}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_regular_rule_variants_keep_only_accepted_rule_provenance(
    client_a_clerk, fully_reviewed_new_property_import, seed_data
) -> None:
    """A rejected regular rule variant must never become canonical provenance."""
    item = fully_reviewed_new_property_import.item
    valid_rule = {
        "title": "Heizkosten",
        "topic": "heating_costs",
        "summary": "Heizkosten werden nach Verbrauch und Fläche verteilt.",
        "scope": {"all_units": True},
        "effect": {"administrative_note": "Für die Abrechnung berücksichtigen."},
        "proposed_status": "EFFECTIVE",
        "supersedes_subject_keys": [],
        "conflicts_with_subject_keys": [],
        "clarification_needed": False,
    }
    generated, _issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{
            "document_key": "main",
            "document_type": "DECLARATION",
            "title": "Teilungserklärung",
        }]),
        ReconciliationOutput(
            facts=[],
            rules=[
                RulePayload(
                    subject_key="rule:heating",
                    evidence=[{
                        "document_key": "main",
                        "pdf_page": 1,
                        "quote": "Heizkosten nach Verbrauch.",
                    }],
                    confidence=0.91,
                    **valid_rule,
                ),
                RulePayload(
                    subject_key="rule:heating",
                    topic="heating_costs",
                    title="Heizkosten nach Fläche",
                    summary="Heizkosten werden nach Fläche verteilt.",
                    scope={"all_units": True},
                    effect={"administrative_note": "Alternative für die Abrechnung."},
                    evidence=[{
                        "document_key": "main",
                        "pdf_page": 1,
                        "quote": "Heizkosten nach Fläche.",
                    }],
                    confidence=0.91,
                    clarification_needed=False,
                ),
            ],
            additional_facts=[],
        ),
        (),
        {"main": fully_reviewed_new_property_import.main.id},
        None,
        (),
    )
    generated_rules = [candidate for candidate in generated if candidate.candidate_type == "RULE"]
    assert len(generated_rules) == 2
    accepted_draft = next(
        candidate for candidate in generated_rules
        if candidate.proposed_value_json["title"] == "Heizkosten"
    )
    rejected_draft = next(
        candidate for candidate in generated_rules
        if candidate.proposed_value_json["title"] == "Heizkosten nach Fläche"
    )
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            heating = await session.scalar(
                select(DeclarationCandidate).where(
                    DeclarationCandidate.import_id == item.id,
                    DeclarationCandidate.stable_key == "rule:heating",
                )
            )
            assert heating is not None
            heating.stable_key = accepted_draft.stable_key
            heating.proposed_value_json = accepted_draft.proposed_value_json
            heating.confidence = accepted_draft.confidence
            heating.review_status = "PENDING"
            heating.reviewed_by = None
            heating.reviewed_at = None
            heating.corrected_value_json = None
            heating.review_comment = None
            await session.execute(
                delete(DeclarationCandidateEvidence).where(
                    DeclarationCandidateEvidence.candidate_id == heating.id
                )
            )
            for source in accepted_draft.evidence:
                session.add(DeclarationCandidateEvidence(
                    organization_id=item.organization_id,
                    candidate_id=heating.id,
                    document_id=source.document_id,
                    pdf_page=source.pdf_page,
                    printed_page=source.printed_page,
                    section_ref=source.section_ref,
                    quote=source.quote,
                    field_path=source.field_path,
                ))
            rejected_candidate = DeclarationCandidate(
                organization_id=item.organization_id,
                import_id=item.id,
                stable_key=rejected_draft.stable_key,
                candidate_type=rejected_draft.candidate_type,
                subject_key=rejected_draft.subject_key,
                field_key=rejected_draft.field_key,
                operation=rejected_draft.operation,
                proposed_value_json=rejected_draft.proposed_value_json,
                confidence=rejected_draft.confidence,
                risk_class=rejected_draft.risk_class,
                requires_individual_review=rejected_draft.requires_individual_review,
                review_status="PENDING",
            )
            session.add(rejected_candidate)
            await session.flush()
            for source in rejected_draft.evidence:
                session.add(DeclarationCandidateEvidence(
                    organization_id=item.organization_id,
                    candidate_id=rejected_candidate.id,
                    document_id=source.document_id,
                    pdf_page=source.pdf_page,
                    printed_page=source.printed_page,
                    section_ref=source.section_ref,
                    quote=source.quote,
                    field_path=source.field_path,
                ))
            session.add_all([
                DeclarationCandidateIssue(
                    organization_id=item.organization_id,
                    import_id=item.id,
                    candidate_id=candidate.id,
                    code="DUPLICATE_RULE_SUBJECT",
                    severity="BLOCKER",
                    message="Zwei Regelvorschläge betreffen denselben Gegenstand.",
                    resolution_status="OPEN",
                )
                for candidate in (heating, rejected_candidate)
            ])
            await session.commit()

        preview = await client_a_clerk.get(
            f"/api/declaration-imports/{item.id}/approval-preview"
        )
        assert preview.status_code == 200
        assert preview.json()["can_approve"] is False

        corrected = await client_a_clerk.patch(
            f"/api/declaration-imports/{item.id}/candidates/{heating.id}",
            json={
                "draft_revision": item.draft_revision,
                "decision": "CORRECTED",
                "corrected_value": valid_rule,
            },
        )
        assert corrected.status_code == 200
        rejected = await client_a_clerk.patch(
            f"/api/declaration-imports/{item.id}/candidates/{rejected_candidate.id}",
            json={
                "draft_revision": corrected.json()["draft_revision"],
                "decision": "REJECTED",
                "comment": "Doppelte Alternative verworfen.",
            },
        )
        assert rejected.status_code == 200
        resolved_preview = await client_a_clerk.get(
            f"/api/declaration-imports/{item.id}/approval-preview"
        )
        assert resolved_preview.status_code == 200
        assert resolved_preview.json()["can_approve"] is True
        assert "DUPLICATE_RULE_SUBJECT" not in {
            issue["code"] for issue in resolved_preview.json()["issues"]
        }

        approved = await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={
                "draft_revision": rejected.json()["draft_revision"],
                "idempotency_key": "colliding-rule-approval-0001",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["summary"]["rules_created"] == 1
        property_id = uuid.UUID(approved.json()["property_id"])
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            rules = (await session.scalars(
                select(PropertyRule).where(PropertyRule.property_id == property_id)
            )).all()
            sources = (await session.scalars(
                select(PropertyRuleSource).where(PropertyRuleSource.property_id == property_id)
            )).all()
            assert len(rules) == 1
            assert [source.quote for source in sources] == ["Heizkosten nach Verbrauch."]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_property_approval_rejects_different_key_after_apply(
    client_a_clerk, fully_reviewed_new_property_import
) -> None:
    item = fully_reviewed_new_property_import.item
    first = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "new-property-approval-0001"},
    )
    assert first.status_code == 200
    conflict = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "new-property-approval-different"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"code": "ALREADY_APPLIED"}


@pytest.mark.asyncio
async def test_new_property_approval_rejects_archived_document_without_writes(
    client_a_clerk, fully_reviewed_new_property_import, seed_data
) -> None:
    from app.core.config import settings
    from app.declarations.approval import deterministic_id

    item = fully_reviewed_new_property_import.item
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            document = await session.scalar(
                select(PropertyDocument).where(PropertyDocument.id == fully_reviewed_new_property_import.main.id)
            )
            assert document is not None
            document.archived_at = datetime.now(timezone.utc)
            await session.commit()
    finally:
        await engine.dispose()

    response = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "archived-document"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "DOCUMENT_ARCHIVED"}

    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            assert await session.scalar(
                select(Property).where(
                    Property.id == deterministic_id(seed_data["org_a_id"], item.id, "property", "new")
                )
            ) is None
            import_row = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == item.id))
            assert import_row is not None and import_row.status == "NEEDS_REVIEW"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_property_approval_rejects_stale_revision(
    client_a_clerk, fully_reviewed_new_property_import
) -> None:
    item = fully_reviewed_new_property_import.item
    response = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision - 1, "idempotency_key": "new-property-approval-stale"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "STALE_DRAFT_REVISION"}


@pytest.mark.asyncio
async def test_new_property_approval_rolls_back_canonical_graph_on_late_failure(
    client_a_clerk, fully_reviewed_new_property_import, monkeypatch, seed_data
) -> None:
    from app.repositories.property_knowledge_repo import PropertyKnowledgeRepository
    from app.declarations.approval import deterministic_id

    item = fully_reviewed_new_property_import.item
    original_apply = PropertyKnowledgeRepository.apply

    async def fail_after_canonical_flush(self, *args, **kwargs):
        await original_apply(self, *args, **kwargs)
        raise RuntimeError("forced-late-approval-failure")

    monkeypatch.setattr(PropertyKnowledgeRepository, "apply", fail_after_canonical_flush)
    with pytest.raises(RuntimeError, match="forced-late-approval-failure"):
        await client_a_clerk.post(
            f"/api/declaration-imports/{item.id}/approve",
            json={"draft_revision": item.draft_revision, "idempotency_key": "new-property-approval-rollback"},
        )

    from app.core.config import settings
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, seed_data["org_a_id"])
            planned_property_id = deterministic_id(
                seed_data["org_a_id"], item.id, "property", "new"
            )
            assert await session.scalar(select(Property).where(Property.id == planned_property_id)) is None
            import_row = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == item.id))
            assert import_row is not None
            assert import_row.status == "NEEDS_REVIEW"
            assert import_row.approval_result_json is None
            document_rows = await session.scalars(
                select(PropertyDocument).where(
                    PropertyDocument.id.in_(
                        [
                            fully_reviewed_new_property_import.main.id,
                            fully_reviewed_new_property_import.amendment.id,
                        ]
                    )
                )
            )
            documents = document_rows.all()
            assert {document.property_id for document in documents} == {None}
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == item.id, AuditEvent.action == "declaration_import.applied")) == 0
    finally:
        await engine.dispose()


async def _approval_plan_inputs(item: DeclarationImport, organization_id: uuid.UUID):
    """Load a detached, complete draft graph for pure-plan negative cases."""
    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await _set_tenant_context(session, organization_id)
            candidates = (await session.scalars(select(DeclarationCandidate).where(DeclarationCandidate.import_id == item.id))).all()
            evidence_rows = (await session.scalars(select(DeclarationCandidateEvidence).where(DeclarationCandidateEvidence.candidate_id.in_([candidate.id for candidate in candidates])))).all()
            documents = (await session.scalars(
                select(PropertyDocument)
                .join(DeclarationImportDocument, DeclarationImportDocument.document_id == PropertyDocument.id)
                .where(DeclarationImportDocument.import_id == item.id)
            )).all()
            return candidates, evidence_rows, documents
    finally:
        await engine.dispose()


def _evidence_by_candidate(rows: list[DeclarationCandidateEvidence]) -> dict[uuid.UUID, list[DeclarationCandidateEvidence]]:
    result: dict[uuid.UUID, list[DeclarationCandidateEvidence]] = {}
    for row in rows:
        result.setdefault(row.candidate_id, []).append(row)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("property_subject", "AMBIGUOUS_PROPERTY_SUBJECT"),
        ("duplicate_unit_subject", "DUPLICATE_UNIT_SUBJECT"),
        ("duplicate_unit_number", "DUPLICATE_UNIT_NUMBER"),
        ("mea_mismatch", "MIXED_MEA_DENOMINATOR"),
        ("missing_evidence", "EVIDENCE_REQUIRED"),
        ("dangling_unit", "DANGLING_UNIT_REFERENCE"),
        ("dangling_document", "DANGLING_DOCUMENT_REFERENCE"),
        ("dangling_rule", "DANGLING_RULE_REFERENCE"),
        ("self_rule", "RULE_SELF_RELATION"),
        ("duplicate_rule_relation", "DUPLICATE_RULE_RELATION"),
        ("duplicate_document_relation", "DUPLICATE_DOCUMENT_RELATION"),
        ("invalid_allocation", "ALLOCATION_TOTAL_INVALID"),
        ("unsupported", "APPROVAL_SCHEMA_UNSUPPORTED"),
        ("long_property", "APPROVAL_VALUE_TOO_LONG"),
        ("long_unit", "APPROVAL_VALUE_TOO_LONG"),
        ("decimal_scale", "APPROVAL_DECIMAL_OUT_OF_RANGE"),
        ("decimal_long", "APPROVAL_DECIMAL_OUT_OF_RANGE"),
        ("area_unit", "APPROVAL_AREA_UNIT_INVALID"),
        ("archived_document", "DOCUMENT_ARCHIVED"),
        ("target_property", "TARGET_PROPERTY_NOT_ALLOWED"),
        ("rule_evidence_collision", "PROPERTY_RULE_SOURCE_COLLISION"),
        ("fact_evidence_collision", "APPROVED_FACT_SOURCE_COLLISION"),
    ],
)
async def test_new_property_plan_blocks_invalid_graphs_before_writes(
    fully_reviewed_new_property_import, seed_data, case: str, expected: str
) -> None:
    from app.declarations.approval import ApprovalConflict, build_approval_plan

    item = fully_reviewed_new_property_import.item
    candidates, evidence_rows, documents = await _approval_plan_inputs(item, seed_data["org_a_id"])
    by_key = {candidate.stable_key: candidate for candidate in candidates}
    evidence = _evidence_by_candidate(evidence_rows)
    issues = []
    if case == "property_subject":
        by_key["property:city"].subject_key = "property:other"
    elif case == "duplicate_unit_subject":
        duplicate = _candidate(item, stable_key="unit-create:duplicate", candidate_type="UNIT_CREATE", subject_key="unit:1", value=by_key["unit-create:unit:1"].proposed_value_json or {})
        duplicate.id = uuid.uuid4()
        candidates.append(duplicate)
        evidence[duplicate.id] = list(evidence[by_key["unit-create:unit:1"].id])
    elif case == "duplicate_unit_number":
        by_key["unit-create:unit:2"].proposed_value_json["number"] = _text(" 1 ")
    elif case == "mea_mismatch":
        by_key["unit-create:unit:2"].proposed_value_json["mea"] = {"kind": "MEA", "numerator": 500_000, "denominator": 999_999}
    elif case == "missing_evidence":
        evidence[by_key["property:name"].id] = []
    elif case == "dangling_unit":
        by_key["assignment:parking"].proposed_value_json["unit_subject_key"] = _text("unit:missing")
    elif case == "dangling_document":
        by_key["relation:amends"].proposed_value_json["target_document_key"] = str(uuid.uuid4())
    elif case == "dangling_rule":
        by_key["rule:heating"].proposed_value_json["supersedes_subject_keys"] = ["rule:missing"]
    elif case == "self_rule":
        by_key["rule:heating"].proposed_value_json["supersedes_subject_keys"] = ["rule:heating"]
    elif case == "duplicate_rule_relation":
        second = _candidate(
            item,
            stable_key="rule:second",
            candidate_type="RULE",
            subject_key="rule:second",
            value={
                "title": "Zweite Regel",
                "topic": "other",
                "summary": "Weitere Regel.",
                "scope": {"all_units": True},
                "effect": {},
                "proposed_status": "EFFECTIVE",
                "supersedes_subject_keys": [],
                "conflicts_with_subject_keys": [],
                "clarification_needed": False,
            },
        )
        second.id = uuid.uuid4()
        candidates.append(second)
        evidence[second.id] = list(evidence[by_key["rule:heating"].id])
        by_key["rule:heating"].proposed_value_json["supersedes_subject_keys"] = ["rule:second", "rule:second"]
    elif case == "duplicate_document_relation":
        duplicate = _candidate(item, stable_key="relation:amends:duplicate", candidate_type="DOCUMENT_RELATION", subject_key=by_key["relation:amends"].subject_key, value=by_key["relation:amends"].proposed_value_json or {})
        duplicate.id = uuid.uuid4()
        candidates.append(duplicate)
        evidence[duplicate.id] = list(evidence[by_key["relation:amends"].id])
    elif case == "invalid_allocation":
        by_key["rule:heating"].proposed_value_json["effect"]["allocation_formula"]["parts"][1]["share"]["value"] = "20"
    elif case == "unsupported":
        unknown = _candidate(item, stable_key="unknown", candidate_type="UNKNOWN", subject_key="unknown:1", value=_text("x"))
        unknown.id = uuid.uuid4()
        candidates.append(unknown)
        evidence[unknown.id] = list(evidence[by_key["property:name"].id])
    elif case == "long_property":
        by_key["property:name"].proposed_value_json = _text("x" * 256)
    elif case == "long_unit":
        by_key["unit-create:unit:1"].proposed_value_json["number"] = _text("x" * 51)
    elif case == "decimal_scale":
        by_key["unit-create:unit:1"].proposed_value_json["area"] = {"kind": "DECIMAL", "value": "1.001", "unit": "m²"}
    elif case == "decimal_long":
        by_key["unit-create:unit:1"].proposed_value_json["area"] = {"kind": "DECIMAL", "value": "0." + ("0" * 1_000) + "1", "unit": "m²"}
    elif case == "area_unit":
        by_key["unit-create:unit:1"].proposed_value_json["area"] = {"kind": "DECIMAL", "value": "1", "unit": "cm²"}
    elif case == "archived_document":
        documents[0].archived_at = datetime.now(timezone.utc)
    elif case == "target_property":
        item.target_property_id = uuid.uuid4()
    elif case == "rule_evidence_collision":
        heating = by_key["rule:heating"]
        collision = DeclarationCandidateEvidence(
            organization_id=item.organization_id,
            candidate_id=heating.id,
            document_id=evidence[heating.id][0].document_id,
            pdf_page=evidence[heating.id][0].pdf_page,
            printed_page=evidence[heating.id][0].printed_page,
            section_ref=evidence[heating.id][0].section_ref,
            quote="Andere Belegstelle",
            field_path=evidence[heating.id][0].field_path,
        )
        evidence[heating.id].append(collision)
    elif case == "fact_evidence_collision":
        name = by_key["property:name"]
        collision = DeclarationCandidateEvidence(
            organization_id=item.organization_id,
            candidate_id=name.id,
            document_id=evidence[name.id][0].document_id,
            pdf_page=evidence[name.id][0].pdf_page,
            printed_page=evidence[name.id][0].printed_page,
            section_ref=evidence[name.id][0].section_ref,
            quote="Andere Belegstelle",
            field_path=evidence[name.id][0].field_path,
        )
        evidence[name.id].append(collision)
    else:  # pragma: no cover - parameter list is the test contract.
        raise AssertionError(case)

    with pytest.raises(ApprovalConflict) as exc:
        build_approval_plan(item, candidates, evidence, issues, documents)
    assert exc.value.code == expected


@pytest.mark.asyncio
async def test_new_property_plan_preserves_rule_evidence_and_trims_required_text(
    fully_reviewed_new_property_import, seed_data
) -> None:
    from app.declarations.approval import build_approval_plan

    item = fully_reviewed_new_property_import.item
    candidates, evidence_rows, documents = await _approval_plan_inputs(item, seed_data["org_a_id"])
    by_key = {candidate.stable_key: candidate for candidate in candidates}
    by_key["legal:1"].proposed_value_json["reference_type"] = _text("  LAND_REGISTER  ")
    by_key["assignment:parking"].proposed_value_json["assignment_type"] = _text("  SPECIAL_USE  ")
    by_key["assignment:parking"].proposed_value_json["designation"] = _text("  Stellplatz 2  ")
    evidence = _evidence_by_candidate(evidence_rows)

    plan = build_approval_plan(item, candidates, evidence, [], documents)

    legal = plan.legal_references[0]
    assignment = plan.assignments[0]
    assert legal.values["reference_type"] == "LAND_REGISTER"
    assert assignment.values["assignment_type"] == "SPECIAL_USE"
    assert assignment.values["designation"] == "Stellplatz 2"
    heating = next(rule for rule in plan.rules if rule.source_stable_key == "rule:heating")
    assert len(heating.evidence) == 2
    assert len([source for source in plan.fact_sources if source.subject_kind == "RULE"]) == 1


@pytest.mark.asyncio
async def test_new_property_plan_blocks_open_blocker_before_writes(
    fully_reviewed_new_property_import, seed_data
) -> None:
    from app.declarations.approval import ApprovalConflict, build_approval_plan
    from app.models.declaration_candidate import DeclarationCandidateIssue

    item = fully_reviewed_new_property_import.item
    candidates, evidence_rows, documents = await _approval_plan_inputs(item, seed_data["org_a_id"])
    issue = DeclarationCandidateIssue(
        organization_id=item.organization_id,
        import_id=item.id,
        candidate_id=candidates[0].id,
        code="TEST_OPEN_BLOCKER",
        severity="BLOCKER",
        message="must block",
        resolution_status="OPEN",
    )
    with pytest.raises(ApprovalConflict) as exc:
        build_approval_plan(item, candidates, _evidence_by_candidate(evidence_rows), [issue], documents)
    assert exc.value.code == "OPEN_BLOCKER"
