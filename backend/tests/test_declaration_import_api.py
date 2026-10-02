from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
import os
import stat
from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.declaration_import import DeclarationExtractionRun
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
)
from app.models.property import Property
from app.models.unit import Unit
from app.core.config import settings
from app.models.user import User
from app.core.storage import get_store
from app.declarations.fake_provider import FakeDeclarationProvider
from app.declarations.pipeline import DeclarationPipeline
from app.declarations.provider import ProviderUsage
from app.declarations.schemas import (
    CoreExtractionOutput,
    DocumentInventoryOutput,
    EvidencePayload,
    FactPayload,
    IntegerValue,
    MeaFraction,
    ReconciliationOutput,
    RuleEffect,
    RuleExtractionOutput,
    RulePayload,
    RuleScope,
    TargetedRecheckOutput,
    TextValue,
)
from app.workers.declaration_import import DeclarationImportWorker
from tests.declaration_fixtures import make_text_pdf


@pytest.fixture(autouse=True)
def isolated_document_store(tmp_path):
    """Keep the process-global document store isolated between HTTP tests."""
    from app.core import storage

    storage.set_store(storage.LocalFsDocumentStore(tmp_path))
    yield
    storage._store = None


@pytest.fixture(autouse=True)
def enable_declaration_imports(monkeypatch):
    monkeypatch.setattr(settings, "declaration_import_enabled", True)


async def _set_organization(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


async def _assert_db_error(
    session: AsyncSession,
    statement: str,
    parameters: dict,
) -> None:
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(text(statement), parameters)


def _provider_for_uploaded_documents(
    declaration_document_id: str,
    amendment_document_id: str | None = None,
    *,
    street: str = "Hauptstraße 1",
    include_unit: bool = True,
) -> FakeDeclarationProvider:
    """Create an isolated fake only after its uploaded document IDs are known."""
    declaration_evidence = EvidencePayload(
        document_key=declaration_document_id,
        pdf_page=1,
        section="§ 1",
        quote="Hauptstraße 1",
    )
    amendment_evidence = EvidencePayload(
        document_key=amendment_document_id or declaration_document_id,
        pdf_page=1,
        section="§ 12",
        quote="Zutritt für erforderliche Instandhaltung ermöglichen.",
    )
    street_fact = FactPayload(
        subject_key="property:main",
        field="street",
        raw_value=street,
        normalized_value=TextValue(kind="TEXT", value=street),
        confidence=0.99,
        evidence=[declaration_evidence],
    )
    facts = [street_fact]
    if include_unit:
        denominator = FactPayload(
            subject_key="property:main",
            field="mea_denominator",
            raw_value="1.000.000",
            normalized_value=IntegerValue(kind="INTEGER", value=1_000_000),
            confidence=0.99,
            evidence=[declaration_evidence],
        )
        unit_number = FactPayload(
            subject_key="unit:wohnung-1",
            field="number",
            raw_value="1",
            normalized_value=TextValue(kind="TEXT", value="1"),
            confidence=0.98,
            evidence=[declaration_evidence],
        )
        unit_mea = FactPayload(
            subject_key="unit:wohnung-1",
            field="mea",
            raw_value="47.500/1.000.000",
            normalized_value=MeaFraction(
                kind="MEA", numerator=47_500, denominator=1_000_000
            ),
            confidence=0.98,
            evidence=[declaration_evidence],
        )
        facts.extend([denominator, unit_number, unit_mea])
    access_rule = RulePayload(
        subject_key="rule:maintenance-access",
        title="Zutritt für Instandhaltung",
        topic="maintenance_access",
        summary="Eigentümer ermöglichen den erforderlichen Zutritt für Wartung und Reparaturen.",
        scope=RuleScope(all_units=True),
        effect=RuleEffect(
            responsibility=["Sondereigentümer"],
            administrative_note="Bei Terminabstimmungen berücksichtigen.",
        ),
        evidence=[amendment_evidence],
        confidence=0.97,
        clarification_needed=False,
    )
    inventory_documents = [
        {
            "document_key": declaration_document_id,
            "document_type": "DECLARATION",
            "title": "Teilungserklärung",
        }
    ]
    if amendment_document_id is not None:
        inventory_documents.append(
            {
                "document_key": amendment_document_id,
                "document_type": "AMENDMENT",
                "title": "Änderung",
            }
        )
    inventory = DocumentInventoryOutput(documents=inventory_documents)
    core = CoreExtractionOutput(facts=facts)
    rules = RuleExtractionOutput(rules=[access_rule])
    return FakeDeclarationProvider(
        inventory_output=inventory,
        core_output=core,
        rules_output=rules,
        reconciliation_output=ReconciliationOutput(
            facts=facts,
            rules=[access_rule],
            mea_set_status="PARTIAL" if include_unit else "UNKNOWN",
        ),
        recheck_output=TargetedRecheckOutput(),
        usage=ProviderUsage(
            input_tokens=17,
            output_tokens=11,
            cost_usd=Decimal("0.000001"),
            attempt_count=1,
        ),
    )


@pytest_asyncio.fixture
async def tenant_session(
    app_rw_session: AsyncSession,
    seed_data: dict,
) -> AsyncSession:
    await _set_organization(app_rw_session, seed_data["org_a_id"])
    return app_rw_session


@pytest_asyncio.fixture
async def tenant_properties(
    tenant_session: AsyncSession,
    seed_data: dict,
) -> dict[str, uuid.UUID]:
    properties: dict[str, uuid.UUID] = {}
    for key, organization_id, name in (
        ("org_a", seed_data["org_a_id"], "Org A Teilung"),
        ("org_a_secondary", seed_data["org_a_id"], "Org A Zweitobjekt"),
        ("org_b", seed_data["org_b_id"], "Org B Teilung"),
    ):
        property_id = uuid.uuid4()
        await _set_organization(tenant_session, organization_id)
        await tenant_session.execute(
            text(
                "INSERT INTO property ("
                "id, organization_id, name, management_type"
                ") VALUES (:id, :organization_id, :name, 'WEG')"
            ),
            {"id": property_id, "organization_id": organization_id, "name": name},
        )
        properties[key] = property_id
    await _set_organization(tenant_session, seed_data["org_a_id"])
    return properties


@pytest_asyncio.fixture
async def stored_imports(
    tenant_session: AsyncSession,
    tenant_properties: dict[str, uuid.UUID],
    seed_data: dict,
) -> SimpleNamespace:
    import_ids = {"org_a": uuid.uuid4(), "org_b": uuid.uuid4()}
    for key, organization_id, user_id in (
        ("org_a", seed_data["org_a_id"], seed_data["user_a_admin_id"]),
        ("org_b", seed_data["org_b_id"], seed_data["user_b_admin_id"]),
    ):
        await _set_organization(tenant_session, organization_id)
        await tenant_session.execute(
            text(
                "INSERT INTO declaration_import ("
                "id, organization_id, mode, initiated_by, max_cost_usd"
                ") VALUES (:id, :organization_id, 'NEW_PROPERTY', :initiated_by, 10)"
            ),
            {
                "id": import_ids[key],
                "organization_id": organization_id,
                "initiated_by": user_id,
            },
        )
    await _set_organization(tenant_session, seed_data["org_a_id"])
    return SimpleNamespace(**import_ids)


@pytest_asyncio.fixture
async def stored_property_document(
    tenant_session: AsyncSession,
    tenant_properties: dict[str, uuid.UUID],
    seed_data: dict,
) -> SimpleNamespace:
    document_id = uuid.uuid4()
    await tenant_session.execute(
        text(
            "INSERT INTO property_document ("
            "id, organization_id, doc_type, title, mime_type, content_byte_size, "
            "content_sha256, storage_uri, storage_backend, page_count, original_filename"
            ") VALUES ("
            ":id, :organization_id, 'DECLARATION', 'Teilungserklärung', "
            "'application/pdf', 1, :content_sha256, 'test://source.pdf', "
            "'test', 1, 'source.pdf'"
            ")"
        ),
        {
            "id": document_id,
            "organization_id": seed_data["org_a_id"],
            "content_sha256": "a" * 64,
        },
    )
    org_b_document_id = uuid.uuid4()
    await _set_organization(tenant_session, seed_data["org_b_id"])
    await tenant_session.execute(
        text(
            "INSERT INTO property_document ("
            "id, organization_id, doc_type, title, mime_type, content_byte_size, "
            "content_sha256, storage_uri, storage_backend, page_count, original_filename"
            ") VALUES ("
            ":id, :organization_id, 'DECLARATION', 'Org B Teilungserklärung', "
            "'application/pdf', 1, :content_sha256, 'test://org-b-source.pdf', "
            "'test', 1, 'org-b-source.pdf'"
            ")"
        ),
        {
            "id": org_b_document_id,
            "organization_id": seed_data["org_b_id"],
            "content_sha256": "b" * 64,
        },
    )
    await _set_organization(tenant_session, seed_data["org_a_id"])
    return SimpleNamespace(
        id=document_id,
        org_b_document_id=org_b_document_id,
        **tenant_properties,
    )


@pytest.mark.asyncio
async def test_unscoped_app_session_sees_no_import_rows(
    tenant_session: AsyncSession,
    stored_imports: SimpleNamespace,
    seed_data: dict,
) -> None:
    statement = text(
        "SELECT id FROM declaration_import "
        "WHERE id IN (:org_a_import_id, :org_b_import_id)"
    )
    parameters = {
        "org_a_import_id": stored_imports.org_a,
        "org_b_import_id": stored_imports.org_b,
    }

    org_a_rows = await tenant_session.execute(statement, parameters)
    assert set(org_a_rows.scalars()) == {stored_imports.org_a}

    await _set_organization(tenant_session, seed_data["org_b_id"])
    org_b_rows = await tenant_session.execute(statement, parameters)
    assert set(org_b_rows.scalars()) == {stored_imports.org_b}

    await tenant_session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
    unscoped_rows = await tenant_session.execute(statement, parameters)
    assert list(unscoped_rows.scalars()) == []


@pytest.mark.asyncio
async def test_new_tables_force_rls(migrator_session) -> None:
    names = {
        "property_document",
        "declaration_import",
        "declaration_import_document",
        "declaration_extraction_run",
        "declaration_candidate",
        "declaration_candidate_evidence",
        "declaration_candidate_issue",
    }
    result = await migrator_session.execute(
        text(
            "SELECT relname, relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE relname = ANY(:names)"
        ),
        {"names": list(names)},
    )
    rows = {row.relname: row for row in result}
    assert set(rows) == names
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows.values())


@pytest.mark.asyncio
async def test_new_tenant_edges_include_organization_id(migrator_session) -> None:
    edge_names = {
        "fk_property_document_property_tenant",
        "fk_property_document_uploaded_by_tenant",
        "fk_declaration_import_target_property_tenant",
        "fk_declaration_import_applied_property_tenant",
        "fk_declaration_import_initiated_by_tenant",
        "fk_declaration_import_approved_by_tenant",
        "fk_declaration_import_document_import_tenant",
        "fk_declaration_import_document_document_tenant",
        "fk_declaration_extraction_run_import_tenant",
        "fk_declaration_candidate_import_tenant",
        "fk_declaration_candidate_reviewed_by_tenant",
        "fk_declaration_candidate_evidence_candidate_tenant",
        "fk_declaration_candidate_evidence_document_tenant",
        "fk_declaration_candidate_issue_import_tenant",
        "fk_declaration_candidate_issue_candidate_tenant",
        "fk_declaration_candidate_issue_resolved_by_tenant",
    }
    result = await migrator_session.execute(
        text(
            "SELECT constraint_name, child_columns, parent_columns FROM ("
            "SELECT con.conname AS constraint_name, "
            "array_agg(child_attribute.attname ORDER BY child_key.ordinality) "
            "AS child_columns, "
            "array_agg(parent_attribute.attname ORDER BY parent_key.ordinality) "
            "AS parent_columns "
            "FROM pg_constraint con "
            "JOIN LATERAL unnest(con.conkey) WITH ORDINALITY "
            "AS child_key(attnum, ordinality) ON true "
            "JOIN LATERAL unnest(con.confkey) WITH ORDINALITY "
            "AS parent_key(attnum, ordinality) "
            "ON parent_key.ordinality = child_key.ordinality "
            "JOIN pg_attribute child_attribute "
            "ON child_attribute.attrelid = con.conrelid "
            "AND child_attribute.attnum = child_key.attnum "
            "JOIN pg_attribute parent_attribute "
            "ON parent_attribute.attrelid = con.confrelid "
            "AND parent_attribute.attnum = parent_key.attnum "
            "WHERE con.contype = 'f' AND con.conname = ANY(:edge_names) "
            "GROUP BY con.conname"
            ") AS tenant_edges"
        ),
        {"edge_names": list(edge_names)},
    )
    rows = {row.constraint_name: row for row in result}
    assert set(rows) == edge_names
    assert all(
        list(row.child_columns)[0] == "organization_id"
        and list(row.parent_columns) == ["organization_id", "id"]
        for row in rows.values()
    )


@pytest.mark.asyncio
async def test_cross_tenant_property_binding_is_rejected(
    tenant_session: AsyncSession,
    stored_property_document: SimpleNamespace,
) -> None:
    await _assert_db_error(
        tenant_session,
        "UPDATE property_document SET property_id = :property_id WHERE id = :id",
        {"property_id": stored_property_document.org_b, "id": stored_property_document.id},
    )


@pytest.mark.asyncio
async def test_cross_tenant_import_document_reference_is_rejected(
    tenant_session: AsyncSession,
    stored_imports: SimpleNamespace,
    stored_property_document: SimpleNamespace,
    seed_data: dict,
) -> None:
    await _assert_db_error(
        tenant_session,
        "INSERT INTO declaration_import_document ("
        "id, organization_id, import_id, document_id, sequence_no, document_role"
        ") VALUES ("
        ":id, :organization_id, :import_id, :document_id, 1, 'DECLARATION'"
        ")",
        {
            "id": uuid.uuid4(),
            "organization_id": seed_data["org_a_id"],
            "import_id": stored_imports.org_a,
            "document_id": stored_property_document.org_b_document_id,
        },
    )


@pytest.mark.asyncio
async def test_applying_import_requires_approval_but_not_applied_property(
    tenant_session: AsyncSession,
    stored_imports: SimpleNamespace,
    seed_data: dict,
) -> None:
    result = await tenant_session.execute(
        text(
            "UPDATE declaration_import SET status = 'APPLYING', "
            "approved_by = :approved_by, approved_at = now() "
            "WHERE id = :id RETURNING status, applied_property_id"
        ),
        {"approved_by": seed_data["user_a_admin_id"], "id": stored_imports.org_a},
    )
    row = result.one()
    assert row.status == "APPLYING"
    assert row.applied_property_id is None


@pytest.mark.asyncio
async def test_applied_import_requires_and_accepts_application_property(
    tenant_session: AsyncSession,
    stored_imports: SimpleNamespace,
    tenant_properties: dict[str, uuid.UUID],
    seed_data: dict,
) -> None:
    result = await tenant_session.execute(
        text(
            "UPDATE declaration_import SET status = 'APPLIED', "
            "approved_by = :approved_by, approved_at = now(), "
            "applied_property_id = :applied_property_id, "
            "approval_idempotency_key = :approval_idempotency_key, "
            "approval_result_json = CAST(:approval_result_json AS jsonb) "
            "WHERE id = :id RETURNING status, approved_by, approved_at, "
            "applied_property_id, approval_idempotency_key, approval_result_json"
        ),
        {
            "approved_by": seed_data["user_a_admin_id"],
            "applied_property_id": tenant_properties["org_a"],
            "approval_idempotency_key": "approval-result-test-0001",
            "approval_result_json": '{"property_id":"approved"}',
            "id": stored_imports.org_a,
        },
    )
    row = result.one()
    assert row.status == "APPLIED"
    assert row.approved_by == seed_data["user_a_admin_id"]
    assert row.approved_at is not None
    assert row.applied_property_id == tenant_properties["org_a"]
    assert row.approval_idempotency_key == "approval-result-test-0001"
    assert row.approval_result_json == {"property_id": "approved"}


@pytest.mark.asyncio
async def test_applied_import_without_application_property_is_rejected(
    tenant_session: AsyncSession,
    stored_imports: SimpleNamespace,
    seed_data: dict,
) -> None:
    await _assert_db_error(
        tenant_session,
        "UPDATE declaration_import SET status = 'APPLIED', "
        "approved_by = :approved_by, approved_at = now(), "
        "applied_property_id = NULL WHERE id = :id",
        {
            "approved_by": seed_data["user_a_admin_id"],
            "id": stored_imports.org_a,
        },
    )


@pytest.mark.asyncio
async def test_premature_import_approval_metadata_is_rejected(
    tenant_session: AsyncSession,
    seed_data: dict,
) -> None:
    await _assert_db_error(
        tenant_session,
        "INSERT INTO declaration_import ("
        "id, organization_id, mode, initiated_by, approved_by, approved_at, max_cost_usd"
        ") VALUES ("
        ":id, :organization_id, 'NEW_PROPERTY', :initiated_by, :approved_by, now(), 10"
        ")",
        {
            "id": uuid.uuid4(),
            "organization_id": seed_data["org_a_id"],
            "initiated_by": seed_data["user_a_admin_id"],
            "approved_by": seed_data["user_a_admin_id"],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("content_byte_size", 2),
        ("content_sha256", "0" * 64),
        ("storage_uri", "test://changed.pdf"),
        ("storage_backend", "changed"),
        ("mime_type", "application/octet-stream"),
        ("original_filename", "changed.pdf"),
    ],
)
async def test_property_document_content_metadata_is_immutable(
    tenant_session: AsyncSession,
    stored_property_document: SimpleNamespace,
    column: str,
    value: str | int,
) -> None:
    await _assert_db_error(
        tenant_session,
        f"UPDATE property_document SET {column} = :value WHERE id = :id",
        {"value": value, "id": stored_property_document.id},
    )


@pytest.mark.asyncio
async def test_property_document_allows_classification_and_initial_property_binding(
    tenant_session: AsyncSession,
    stored_property_document: SimpleNamespace,
) -> None:
    result = await tenant_session.execute(
        text(
            "UPDATE property_document SET doc_type = 'ANNEX', title = 'Anlage', "
            "document_date = :document_date, deed_reference = 'UR-4', page_count = 2, "
            "archived_at = now(), property_id = :property_id "
            "WHERE id = :id "
            "RETURNING doc_type, title, document_date, deed_reference, page_count, "
            "archived_at, property_id"
        ),
        {
            "document_date": date(2024, 1, 2),
            "property_id": stored_property_document.org_a,
            "id": stored_property_document.id,
        },
    )
    row = result.one()
    assert row.doc_type == "ANNEX"
    assert row.title == "Anlage"
    assert row.document_date == date(2024, 1, 2)
    assert row.deed_reference == "UR-4"
    assert row.page_count == 2
    assert row.archived_at is not None
    assert row.property_id == stored_property_document.org_a


@pytest.mark.asyncio
async def test_property_document_property_binding_cannot_be_rebound_or_unbound(
    tenant_session: AsyncSession,
    stored_property_document: SimpleNamespace,
) -> None:
    await tenant_session.execute(
        text("UPDATE property_document SET property_id = :property_id WHERE id = :id"),
        {"property_id": stored_property_document.org_a, "id": stored_property_document.id},
    )
    await _assert_db_error(
        tenant_session,
        "UPDATE property_document SET property_id = :property_id WHERE id = :id",
        {
            "property_id": stored_property_document.org_a_secondary,
            "id": stored_property_document.id,
        },
    )
    await _assert_db_error(
        tenant_session,
        "UPDATE property_document SET property_id = NULL WHERE id = :id",
        {"id": stored_property_document.id},
    )


@pytest.mark.asyncio
async def test_app_rw_cannot_delete_property_document(
    tenant_session: AsyncSession,
    stored_property_document: SimpleNamespace,
) -> None:
    await _assert_db_error(
        tenant_session,
        "DELETE FROM property_document WHERE id = :id",
        {"id": stored_property_document.id},
    )


@pytest.mark.asyncio
async def test_app_rw_cannot_delete_declaration_extraction_run(
    tenant_session: AsyncSession,
    stored_imports: SimpleNamespace,
    seed_data: dict,
) -> None:
    run_id = uuid.uuid4()
    await tenant_session.execute(
        text(
            "INSERT INTO declaration_extraction_run ("
            "id, organization_id, import_id, provider, model_name, prompt_version, "
            "schema_version, harness_version"
            ") VALUES ("
            ":id, :organization_id, :import_id, 'test', 'test-model', 'v1', 'v1', 'v1'"
            ")"
        ),
        {
            "id": run_id,
            "organization_id": seed_data["org_a_id"],
            "import_id": stored_imports.org_a,
        },
    )
    await _assert_db_error(
        tenant_session,
        "DELETE FROM declaration_extraction_run WHERE id = :id",
        {"id": run_id},
    )


def test_extraction_run_orm_started_at_has_database_default() -> None:
    assert DeclarationExtractionRun.__table__.c.started_at.server_default is not None


def test_parent_orms_declare_tenant_identity_unique_constraints() -> None:
    for model, constraint_name in (
        (Property, "uq_property_organization_id_id"),
        (User, "uq_user_organization_id_id"),
    ):
        constraint_names = {
            constraint.name for constraint in model.__table__.constraints
        }
        assert constraint_name in constraint_names


# ---------------------------------------------------------------------------
# Task 4: bounded declaration-import upload and lifecycle API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_new_property_import_and_upload_pdf(client_a_admin) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"

    uploaded = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("teilung.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["content_sha256"]
    assert uploaded.json()["property_id"] is None


@pytest.mark.asyncio
async def test_list_import_documents_is_ordered_and_write_role_protected(
    client_a_admin, client_a_readonly
) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    import_id = created.json()["id"]
    for filename, sequence_no in (("zwei.pdf", 2), ("eins.pdf", 1)):
        uploaded = await client_a_admin.post(
            f"/api/declaration-imports/{import_id}/documents",
            files={"file": (filename, b"%PDF-1.4\n%%EOF", "application/pdf")},
            data={"document_role": "DECLARATION", "sequence_no": str(sequence_no)},
        )
        assert uploaded.status_code == 201

    listed = await client_a_admin.get(f"/api/declaration-imports/{import_id}/documents")
    assert listed.status_code == 200
    assert [document["sequence_no"] for document in listed.json()] == [1, 2]
    assert [document["original_filename"] for document in listed.json()] == ["eins.pdf", "zwei.pdf"]

    readonly = await client_a_readonly.get(f"/api/declaration-imports/{import_id}/documents")
    assert readonly.status_code == 403


@pytest.mark.asyncio
async def test_readonly_cannot_create_import(client_a_readonly) -> None:
    response = await client_a_readonly.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rejects_non_pdf_magic(client_a_admin) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    response = await client_a_admin.post(
        f"/api/declaration-imports/{created.json()['id']}/documents",
        files={"file": ("fake.pdf", b"not-a-pdf", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cross_tenant_import_is_404(client_a_admin, client_b_admin) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    response = await client_b_admin.get(
        f"/api/declaration-imports/{created.json()['id']}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_readonly_cannot_read_unpublished_import(
    client_a_admin, client_a_readonly
) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    response = await client_a_readonly.get(
        f"/api/declaration-imports/{created.json()['id']}"
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_existing_property_requires_target_and_draft_document_stays_unbound(
    client_a_admin,
) -> None:
    missing = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "EXISTING_PROPERTY"}
    )
    assert missing.status_code == 422

    new_property_target = await client_a_admin.post(
        "/api/declaration-imports",
        json={"mode": "NEW_PROPERTY", "target_property_id": str(uuid.uuid4())},
    )
    assert new_property_target.status_code == 422

    property_response = await client_a_admin.post(
        "/api/properties",
        json={"name": "Bestandsobjekt", "management_type": "WEG"},
    )
    assert property_response.status_code == 201
    created = await client_a_admin.post(
        "/api/declaration-imports",
        json={
            "mode": "EXISTING_PROPERTY",
            "target_property_id": property_response.json()["id"],
        },
    )
    assert created.status_code == 201
    uploaded = await client_a_admin.post(
        f"/api/declaration-imports/{created.json()['id']}/documents",
        files={"file": ("nachtrag.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        data={"document_role": "AMENDMENT", "sequence_no": "1"},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["property_id"] is None


@pytest.mark.asyncio
async def test_start_is_idempotent_and_queues_without_extraction(client_a_admin) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    import_id = created.json()["id"]
    empty_start = await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert empty_start.status_code == 422

    uploaded = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("teilung.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert uploaded.status_code == 201

    started = await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert started.status_code == 200
    assert started.json()["status"] == "QUEUED"
    repeated = await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "QUEUED"
    cancelled = await client_a_admin.post(f"/api/declaration-imports/{import_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_upload_rejects_duplicate_sequence_and_non_draft_update(client_a_admin) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    import_id = created.json()["id"]
    first = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("one.pdf", b"%PDF-1.4\none", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert first.status_code == 201
    duplicate = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("two.pdf", b"%PDF-1.4\ntwo", "application/pdf")},
        data={"document_role": "AMENDMENT", "sequence_no": "1"},
    )
    assert duplicate.status_code == 409

    metadata = await client_a_admin.patch(
        f"/api/declaration-imports/{import_id}/documents/{first.json()['id']}",
        json={
            "title": "Erste Fassung",
            "document_role": "AMENDMENT",
            "sequence_no": 2,
        },
    )
    assert metadata.status_code == 200
    assert metadata.json()["title"] == "Erste Fassung"
    assert metadata.json()["document_role"] == "AMENDMENT"
    assert metadata.json()["sequence_no"] == 2

    started = await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert started.status_code == 200
    update = await client_a_admin.patch(
        f"/api/declaration-imports/{import_id}/documents/{first.json()['id']}",
        json={"title": "Geänderte Überschrift"},
    )
    assert update.status_code == 409
    cancelled = await client_a_admin.post(f"/api/declaration-imports/{import_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cancelled_import_returns_stable_start_error_and_hides_candidates_from_readonly(
    client_a_admin, client_a_readonly
) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    import_id = created.json()["id"]
    cancelled = await client_a_admin.post(f"/api/declaration-imports/{import_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    start = await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert start.status_code == 409
    assert start.json()["detail"] == "IMPORT_CANCELLED"
    candidates = await client_a_readonly.get(
        f"/api/declaration-imports/{import_id}/candidates"
    )
    assert candidates.status_code == 403


@pytest.mark.asyncio
async def test_public_new_property_import_reaches_review_with_two_documents_and_no_canonical_writes(
    client_a_admin,
    client_a_readonly,
    tenant_session: AsyncSession,
) -> None:
    before_properties = await tenant_session.scalar(select(func.count()).select_from(Property))
    before_units = await tenant_session.scalar(select(func.count()).select_from(Unit))

    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    uploaded_declaration = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={
            "file": (
                "teilungserklaerung.pdf",
                make_text_pdf("Teilungserklärung", "Hauptstraße 1", "Einheit 1"),
                "application/pdf",
            )
        },
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    uploaded_amendment = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={
            "file": (
                "aenderung.pdf",
                make_text_pdf(
                    "Änderung",
                    "Zutritt für erforderliche Instandhaltung ermöglichen.",
                ),
                "application/pdf",
            )
        },
        data={"document_role": "AMENDMENT", "sequence_no": "2"},
    )
    assert uploaded_declaration.status_code == uploaded_amendment.status_code == 201
    declaration_document_id = uploaded_declaration.json()["id"]
    amendment_document_id = uploaded_amendment.json()["id"]

    started = await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
    assert started.status_code == 200
    provider = _provider_for_uploaded_documents(
        declaration_document_id, amendment_document_id
    )
    worker = DeclarationImportWorker(DeclarationPipeline(provider, store=get_store()))
    assert (await worker.run_once()).code == "PROCESSED"

    status = await client_a_admin.get(f"/api/declaration-imports/{import_id}")
    candidates = await client_a_admin.get(f"/api/declaration-imports/{import_id}/candidates")
    assert status.status_code == candidates.status_code == 200
    payload = status.json()
    assert payload["status"] == "NEEDS_REVIEW"
    assert payload["input_tokens"] > 0
    assert payload["output_tokens"] > 0
    assert Decimal(payload["estimated_cost_usd"]) > 0
    # Four fixed text passes plus one deterministic targeted recheck for the
    # intentionally partial MEA set.
    assert payload["attempt_count"] == 5
    candidate_payloads = candidates.json()
    assert any(
        candidate["field_key"] == "street"
        and candidate["proposed_value_json"] == {"kind": "TEXT", "value": "Hauptstraße 1"}
        for candidate in candidate_payloads
    )
    unit_candidate = next(
        candidate for candidate in candidate_payloads
        if candidate["candidate_type"] == "UNIT_CREATE"
    )
    assert unit_candidate["proposed_value_json"]["mea"] == {
        "kind": "MEA", "numerator": 47_500, "denominator": 1_000_000
    }
    rule_candidate = next(
        candidate for candidate in candidate_payloads
        if candidate["candidate_type"] == "RULE"
    )
    assert rule_candidate["proposed_value_json"]["topic"] == "maintenance_access"
    assert rule_candidate["proposed_value_json"]["title"] == "Zutritt für Instandhaltung"
    assert rule_candidate["proposed_value_json"]["summary"] == (
        "Eigentümer ermöglichen den erforderlichen Zutritt für Wartung und Reparaturen."
    )
    assert rule_candidate["proposed_value_json"]["scope"] == {
        "all_units": True,
        "unit_subject_keys": [],
        "building_parts": [],
        "participant_groups": [],
        "additional_scope": [],
    }
    assert rule_candidate["proposed_value_json"]["effect"]["responsibility"] == [
        "Sondereigentümer"
    ]
    assert rule_candidate["proposed_value_json"]["effect"]["administrative_note"] == (
        "Bei Terminabstimmungen berücksichtigen."
    )

    evidence_rows = await tenant_session.execute(
        select(
            DeclarationCandidateEvidence.document_id,
            DeclarationCandidateEvidence.pdf_page,
        )
        .join(
            DeclarationCandidate,
            DeclarationCandidate.id == DeclarationCandidateEvidence.candidate_id,
        )
        .where(DeclarationCandidate.import_id == uuid.UUID(import_id))
    )
    assert set(evidence_rows.all()) >= {
        (uuid.UUID(declaration_document_id), 1),
        (uuid.UUID(amendment_document_id), 1),
    }
    after_properties = await tenant_session.scalar(select(func.count()).select_from(Property))
    after_units = await tenant_session.scalar(select(func.count()).select_from(Unit))
    assert (after_properties, after_units) == (before_properties, before_units)
    assert (await client_a_readonly.get(f"/api/declaration-imports/{import_id}")).status_code == 403
    assert (
        await client_a_readonly.get(f"/api/declaration-imports/{import_id}/candidates")
    ).status_code == 403


@pytest.mark.asyncio
async def test_existing_property_import_proposes_street_update_without_deleting_omitted_unit(
    client_a_admin,
    tenant_session: AsyncSession,
) -> None:
    property_response = await client_a_admin.post(
        "/api/properties",
        json={
            "name": "Bestandsobjekt",
            "street": "Alte Straße 9",
            "management_type": "WEG",
        },
    )
    assert property_response.status_code == 201
    property_id = property_response.json()["id"]
    unit_response = await client_a_admin.post(
        "/api/units",
        json={
            "property_id": property_id,
            "number": "bestehend-7",
            "usage_type": "WOHNUNG",
        },
    )
    assert unit_response.status_code == 201
    before_properties = await tenant_session.scalar(select(func.count()).select_from(Property))
    before_units = await tenant_session.scalar(select(func.count()).select_from(Unit))

    created = await client_a_admin.post(
        "/api/declaration-imports",
        json={"mode": "EXISTING_PROPERTY", "target_property_id": property_id},
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    uploaded = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={
            "file": (
                "bestand.pdf",
                make_text_pdf("Teilungserklärung", "Neue Straße 10"),
                "application/pdf",
            )
        },
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert uploaded.status_code == 201
    assert (await client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})).status_code == 200
    provider = _provider_for_uploaded_documents(
        uploaded.json()["id"], street="Neue Straße 10", include_unit=False
    )
    worker = DeclarationImportWorker(DeclarationPipeline(provider, store=get_store()))
    assert (await worker.run_once()).code == "PROCESSED"

    status = await client_a_admin.get(f"/api/declaration-imports/{import_id}")
    candidates = await client_a_admin.get(f"/api/declaration-imports/{import_id}/candidates")
    assert status.status_code == candidates.status_code == 200
    assert status.json()["status"] == "NEEDS_REVIEW"
    street_update = next(
        candidate for candidate in candidates.json()
        if candidate["field_key"] == "street"
    )
    assert street_update["operation"] == "UPDATE"
    assert street_update["previous_value_json"] == {
        "kind": "TEXT", "value": "Alte Straße 9"
    }
    assert not any(candidate["operation"] == "DELETE" for candidate in candidates.json())
    canonical_property = await tenant_session.scalar(
        select(Property).where(Property.id == uuid.UUID(property_id))
    )
    canonical_unit = await tenant_session.scalar(
        select(Unit).where(Unit.id == uuid.UUID(unit_response.json()["id"]))
    )
    assert canonical_property is not None and canonical_property.street == "Alte Straße 9"
    assert canonical_unit is not None and canonical_unit.number == "bestehend-7"
    after_properties = await tenant_session.scalar(select(func.count()).select_from(Property))
    after_units = await tenant_session.scalar(select(func.count()).select_from(Unit))
    assert (after_properties, after_units) == (before_properties, before_units)


@pytest.mark.asyncio
async def test_bounded_reader_rejects_a_file_over_the_limit() -> None:
    from app.core.uploads import read_upload_limited

    upload = UploadFile(filename="large.pdf", file=BytesIO(b"12345"))
    with pytest.raises(HTTPException) as exc_info:
        await read_upload_limited(upload, max_bytes=4)
    assert exc_info.value.status_code == 413


def test_streaming_integrity_checks_hash_and_size(tmp_path) -> None:
    from app.core.storage import (
        LocalFsDocumentStore,
        compute_sha256,
        make_storage_key,
        verify_document_integrity_and_size,
    )

    content = b"%PDF-1.4\n%%EOF"
    sha256 = compute_sha256(content)
    store = LocalFsDocumentStore(tmp_path)
    key = make_storage_key("test-org", sha256)
    store.put(key, content, content_type="application/pdf")

    assert verify_document_integrity_and_size(sha256, len(content), key, store)
    assert not verify_document_integrity_and_size(sha256, len(content) + 1, key, store)


def test_local_store_uses_owner_only_file_and_directory_modes(tmp_path) -> None:
    from app.core.storage import LocalFsDocumentStore, compute_sha256, make_storage_key

    content = b"%PDF-1.4\n%%EOF"
    sha256 = compute_sha256(content)
    store = LocalFsDocumentStore(tmp_path)
    key = make_storage_key("tenant-a", sha256)
    store.put(key, content, content_type="application/pdf")

    stored_path = tmp_path / key
    assert stat.S_IMODE(stored_path.stat().st_mode) == 0o400
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "tenant-a").stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_path.parent.stat().st_mode) == 0o700


def test_local_store_hardens_an_existing_matching_blob_and_parent_directories(tmp_path) -> None:
    from app.core.storage import LocalFsDocumentStore, compute_sha256, make_storage_key

    content = b"%PDF-1.4\n%%EOF"
    key = make_storage_key("tenant-a", compute_sha256(content))
    stored_path = tmp_path / key
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(content)
    tmp_path.chmod(0o755)
    stored_path.parent.parent.chmod(0o755)
    stored_path.parent.chmod(0o755)
    stored_path.chmod(0o444)

    LocalFsDocumentStore(tmp_path).put(key, content, content_type="application/pdf")

    assert stored_path.read_bytes() == content
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_path.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_path.stat().st_mode) == 0o400


def test_local_store_repairs_a_corrupt_existing_content_addressed_blob(tmp_path) -> None:
    from app.core.storage import LocalFsDocumentStore, compute_sha256, make_storage_key

    content = b"%PDF-1.4\n%%EOF"
    key = make_storage_key("tenant-a", compute_sha256(content))
    stored_path = tmp_path / key
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"partial")
    stored_path.chmod(0o400)

    stored = LocalFsDocumentStore(tmp_path).put(key, content, content_type="application/pdf")

    assert stored.byte_size == len(content)
    assert stored_path.read_bytes() == content
    assert stat.S_IMODE(stored_path.stat().st_mode) == 0o400


def test_local_store_cleans_failed_atomic_write_and_allows_retry(tmp_path, monkeypatch) -> None:
    from app.core import storage

    content = b"%PDF-1.4\n%%EOF"
    key = storage.make_storage_key("tenant-a", storage.compute_sha256(content))
    stored_path = tmp_path / key
    store = storage.LocalFsDocumentStore(tmp_path)
    original_replace = storage.os.replace

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        store.put(key, content, content_type="application/pdf")
    assert not stored_path.exists()
    assert list(stored_path.parent.glob(".*.tmp")) == []

    monkeypatch.setattr(storage.os, "replace", original_replace)
    store.put(key, content, content_type="application/pdf")
    assert stored_path.read_bytes() == content
    assert list(stored_path.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize("failure_point", ["fchmod", "fdopen"])
def test_local_store_closes_temp_descriptor_when_setup_fails(
    tmp_path, monkeypatch, failure_point
) -> None:
    from app.core import storage

    content = b"%PDF-1.4\n%%EOF"
    key = storage.make_storage_key("tenant-a", storage.compute_sha256(content))
    stored_path = tmp_path / key
    store = storage.LocalFsDocumentStore(tmp_path)
    captured_descriptors: list[int] = []

    with monkeypatch.context() as scoped_patch:
        if failure_point == "fchmod":
            def fail_fchmod(file_descriptor, mode):
                captured_descriptors.append(file_descriptor)
                raise OSError("simulated fchmod failure")

            scoped_patch.setattr(storage.os, "fchmod", fail_fchmod)
        else:
            def fail_fdopen(file_descriptor, *args, **kwargs):
                captured_descriptors.append(file_descriptor)
                raise OSError("simulated fdopen failure")

            scoped_patch.setattr(storage.os, "fdopen", fail_fdopen)

        with pytest.raises(OSError, match=f"simulated {failure_point} failure"):
            store.put(key, content, content_type="application/pdf")

    assert len(captured_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(captured_descriptors[0])
    assert not stored_path.exists()
    assert list(stored_path.parent.glob(".*.tmp")) == []

    store.put(key, content, content_type="application/pdf")
    assert stored_path.read_bytes() == content


def test_local_store_fsyncs_file_and_parent_directory_and_closes_directory_fd(
    tmp_path, monkeypatch
) -> None:
    from app.core import storage

    content = b"%PDF-1.4\n%%EOF"
    key = storage.make_storage_key("tenant-a", storage.compute_sha256(content))
    synced_kinds: list[int] = []
    closed_kinds: list[int] = []
    original_fsync = storage.os.fsync
    original_close = storage.os.close

    def record_fsync(file_descriptor):
        synced_kinds.append(stat.S_IFMT(os.fstat(file_descriptor).st_mode))
        return original_fsync(file_descriptor)

    def record_close(file_descriptor):
        closed_kinds.append(stat.S_IFMT(os.fstat(file_descriptor).st_mode))
        return original_close(file_descriptor)

    monkeypatch.setattr(storage.os, "fsync", record_fsync)
    monkeypatch.setattr(storage.os, "close", record_close)
    storage.LocalFsDocumentStore(tmp_path).put(key, content, content_type="application/pdf")

    assert synced_kinds.count(stat.S_IFREG) == 1
    assert synced_kinds.count(stat.S_IFDIR) == 1
    assert closed_kinds.count(stat.S_IFDIR) == 1


def test_local_store_retries_parent_directory_sync_for_existing_matching_blob(
    tmp_path, monkeypatch
) -> None:
    from app.core import storage

    content = b"%PDF-1.4\n%%EOF"
    key = storage.make_storage_key("tenant-a", storage.compute_sha256(content))
    store = storage.LocalFsDocumentStore(tmp_path)
    store.put(key, content, content_type="application/pdf")
    original_fsync = storage.os.fsync

    def fail_directory_fsync(file_descriptor):
        if stat.S_ISDIR(os.fstat(file_descriptor).st_mode):
            raise OSError("simulated directory fsync failure")
        return original_fsync(file_descriptor)

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(storage.os, "fsync", fail_directory_fsync)
        with pytest.raises(OSError, match="simulated directory fsync failure"):
            store.put(key, content, content_type="application/pdf")

    store.put(key, content, content_type="application/pdf")


@pytest.mark.asyncio
async def test_upload_rejects_overlong_filename_before_creating_blob_or_document(
    client_a_admin, app_rw_session, seed_data, tmp_path
) -> None:
    await _set_organization(app_rw_session, seed_data["org_a_id"])
    document_count_before = await app_rw_session.scalar(
        text("SELECT count(*) FROM property_document")
    )
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    filename = f"{'x' * 252}.pdf"

    response = await client_a_admin.post(
        f"/api/declaration-imports/{created.json()['id']}/documents",
        files={"file": (filename, b"%PDF-1.4\n%%EOF", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "original_filename must be at most 255 characters"
    assert list(tmp_path.rglob("*.bin")) == []
    await _set_organization(app_rw_session, seed_data["org_a_id"])
    document_count = await app_rw_session.scalar(text("SELECT count(*) FROM property_document"))
    assert document_count == document_count_before


@pytest.mark.asyncio
async def test_multipart_reader_rejects_over_limit_and_empty_file(
    client_a_admin, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "declaration_import_enabled", True)
    monkeypatch.setattr(settings, "declaration_max_pdf_bytes", 4)
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    oversized = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("oversized.pdf", b"%PDF-", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert oversized.status_code == 413
    empty = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_property_document_service_rejects_direct_oversized_bytes(
    app_rw_session, seed_data, monkeypatch
) -> None:
    from app.services import property_document_service
    from app.core.config import settings

    monkeypatch.setattr(settings, "declaration_max_pdf_bytes", 4)
    await _set_organization(app_rw_session, seed_data["org_a_id"])
    service = property_document_service.PropertyDocumentService(
        app_rw_session, seed_data["org_a_id"], seed_data["user_a_admin_id"]
    )
    with pytest.raises(HTTPException) as exc_info:
        await service.upload(
            uuid.uuid4(),
            b"%PDF-",
            original_filename="oversized.pdf",
            mime_type="application/pdf",
            document_role="DECLARATION",
            sequence_no=1,
        )
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_declaration_import_disabled_blocks_create_upload_and_start(
    client_a_admin, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "declaration_import_enabled", True)
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]

    monkeypatch.setattr(settings, "declaration_import_enabled", False)
    blocked_create = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    blocked_upload = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("teilung.pdf", b"%PDF-1.4\\n%%EOF", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    blocked_start = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"}
    )

    for response in (blocked_create, blocked_upload, blocked_start):
        assert response.status_code == 503
        assert response.json()["detail"] == "DECLARATION_IMPORT_DISABLED"


@pytest.mark.asyncio
async def test_declaration_import_disabled_does_not_requeue_retryable_import(
    client_a_admin, monkeypatch, seed_data
) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = uuid.UUID(created.json()["id"])

    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await _set_organization(session, seed_data["org_a_id"])
                await session.execute(
                    text(
                        "UPDATE declaration_import "
                        "SET status = 'FAILED', error_code = 'PROVIDER_TIMEOUT' "
                        "WHERE id = :import_id"
                    ),
                    {"import_id": import_id},
                )

        monkeypatch.setattr(settings, "declaration_import_enabled", False)
        response = await client_a_admin.post(
            f"/api/declaration-imports/{import_id}/retry", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"}
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "DECLARATION_IMPORT_DISABLED"

        async with factory() as session:
            await _set_organization(session, seed_data["org_a_id"])
            status = await session.scalar(
                text("SELECT status FROM declaration_import WHERE id = :import_id"),
                {"import_id": import_id},
            )
            assert status == "FAILED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code,expected", [("PROVIDER_INCOMPLETE",200), ("PROVIDER_CONNECTION_ERROR",200), ("OPENAI_REQUEST_FAILED",409)])
async def test_manual_retry_provider_outcomes_use_durable_queue_receipts(client_a_admin, seed_data, error_code, expected):
    from tests.test_scoped_account_statements import mutate
    created = await client_a_admin.post('/api/declaration-imports', json={'mode':'NEW_PROPERTY'})
    job = uuid.UUID(created.json()['id'])
    await mutate(text("UPDATE declaration_import SET status='FAILED',error_code=:error WHERE id=:job"), {'job':job, 'error':error_code})
    response = await client_a_admin.post(f'/api/declaration-imports/{job}/retry', headers={'Idempotency-Key':str(uuid.uuid4())})
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()['status'] == 'QUEUED'
        assert response.json()['queue_request_id'] == response.json()['command_receipt_id']
    else:
        assert response.json()['detail'] == 'IMPORT_NOT_RETRYABLE'


@pytest.mark.asyncio
async def test_document_limit_uses_runtime_setting(client_a_admin, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "declaration_import_enabled", True)
    monkeypatch.setattr(settings, "declaration_max_documents", 1)
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    first = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("teilung.pdf", b"%PDF-1.4\\n%%EOF", "application/pdf")},
        data={"document_role": "DECLARATION", "sequence_no": "1"},
    )
    second = await client_a_admin.post(
        f"/api/declaration-imports/{import_id}/documents",
        files={"file": ("nachtrag.pdf", b"%PDF-1.4\\n%%EOF", "application/pdf")},
        data={"document_role": "AMENDMENT", "sequence_no": "2"},
    )

    assert first.status_code == 201
    assert second.status_code == 422
    assert second.json()["detail"] == "DOCUMENT_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_parallel_uploads_respect_document_limit(
    client_a_admin, app_rw_session, seed_data
) -> None:
    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    for sequence_no in range(1, 10):
        response = await client_a_admin.post(
            f"/api/declaration-imports/{import_id}/documents",
            files={
                "file": (
                    f"{sequence_no}.pdf",
                    f"%PDF-1.4\\n{sequence_no}".encode(),
                    "application/pdf",
                )
            },
            data={"document_role": "DECLARATION", "sequence_no": str(sequence_no)},
        )
        assert response.status_code == 201

    async def upload(sequence_no: int) -> int:
        response = await client_a_admin.post(
            f"/api/declaration-imports/{import_id}/documents",
            files={
                "file": (
                    f"{sequence_no}.pdf",
                    f"%PDF-1.4\\n{sequence_no}".encode(),
                    "application/pdf",
                )
            },
            data={"document_role": "AMENDMENT", "sequence_no": str(sequence_no)},
        )
        return response.status_code

    statuses = await asyncio.gather(upload(10), upload(11))
    assert sorted(statuses) == [201, 422]
    await _set_organization(app_rw_session, seed_data["org_a_id"])
    count = await app_rw_session.scalar(
        text(
            "SELECT count(*) FROM declaration_import_document "
            "WHERE import_id = :import_id"
        ),
        {"import_id": import_id},
    )
    assert count == 10


@pytest.mark.asyncio
async def test_parallel_upload_and_start_are_serialized(client_a_admin, monkeypatch) -> None:
    from app.services.property_document_service import PropertyDocumentService

    created = await client_a_admin.post(
        "/api/declaration-imports", json={"mode": "NEW_PROPERTY"}
    )
    assert created.status_code == 201
    import_id = created.json()["id"]
    entered_upload = asyncio.Event()
    release_upload = asyncio.Event()
    original_upload = PropertyDocumentService.upload

    async def delayed_upload(self, *args, **kwargs):
        entered_upload.set()
        await release_upload.wait()
        return await original_upload(self, *args, **kwargs)

    monkeypatch.setattr(PropertyDocumentService, "upload", delayed_upload)
    upload_task = asyncio.create_task(
        client_a_admin.post(
            f"/api/declaration-imports/{import_id}/documents",
            files={"file": ("one.pdf", b"%PDF-1.4\\none", "application/pdf")},
            data={"document_role": "DECLARATION", "sequence_no": "1"},
        )
    )
    start_task = None
    try:
        await asyncio.wait_for(entered_upload.wait(), timeout=2)
        start_task = asyncio.create_task(
            client_a_admin.post(f"/api/declaration-imports/{import_id}/start", headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"})
        )
        await asyncio.sleep(0.05)
        assert not start_task.done()
        release_upload.set()
        uploaded, started = await asyncio.gather(upload_task, start_task)
    finally:
        release_upload.set()
        if not upload_task.done():
            await upload_task
        if start_task is not None and not start_task.done():
            await start_task
    assert uploaded.status_code == 201
    assert started.status_code == 200
    assert started.json()["status"] == "QUEUED"
    cancelled = await client_a_admin.post(f"/api/declaration-imports/{import_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
