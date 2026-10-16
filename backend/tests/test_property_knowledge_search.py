"""Search projection and accounting schema invariants."""

from __future__ import annotations

import os
import json
import uuid
from decimal import Decimal

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.declarations.approval import value_fingerprint
from app.models.approved_fact_source import ApprovedFactSource
from app.models.property_rule import PropertyRule
from app.repositories.property_knowledge_repo import (
    build_fact_search_text,
    build_rule_search_text,
)


def _alembic_config() -> Config:
    config = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    config.set_main_option("sqlalchemy.url", os.environ["HV_DATABASE_URL_SYNC"])
    config.attributes["hv_declaration_test_database"] = True
    config.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    return config


@pytest.fixture
def migration_config_at_head(request, seed_data) -> Config:
    # This destructive historical rehearsal runs only against the disposable DB.
    # Retained source/policy evidence from preceding cases must not be erased by
    # weakening production downgrade guards.
    from tests.conftest import clear_disposable_source_context
    with psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"]) as connection, connection.cursor() as cursor:
        clear_disposable_source_context(cursor)
        cursor.execute("TRUNCATE knowledge_budget_policy,property_source_security")
    config = _alembic_config()
    request.addfinalizer(lambda: command.upgrade(config, "head"))
    return config


def _delete_backfill_graph(ids: dict[str, uuid.UUID]) -> None:
    """Remove the committed migration fixture in FK-safe order at any schema >= 0025."""
    from tests.conftest import clear_disposable_source_context

    with psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"]) as connection:
        with connection.cursor() as cursor:
            clear_disposable_source_context(cursor)
            cursor.execute(
                "DELETE FROM approved_fact_source WHERE source_import_id = %s",
                (str(ids["import"]),),
            )
            cursor.execute(
                "DELETE FROM property_rule_source WHERE property_rule_id = %s",
                (str(ids["rule"]),),
            )
            cursor.execute(
                "DELETE FROM property_rule WHERE id = %s",
                (str(ids["rule"]),),
            )
            cursor.execute(
                "DELETE FROM property_document_relation WHERE id = %s",
                (str(ids["relation"]),),
            )
            cursor.execute(
                "DELETE FROM property_assignment WHERE id = %s",
                (str(ids["assignment"]),),
            )
            cursor.execute(
                "DELETE FROM property_legal_reference WHERE id = %s",
                (str(ids["legal"]),),
            )
            cursor.execute(
                "DELETE FROM declaration_import WHERE id = %s",
                (str(ids["import"]),),
            )
            cursor.execute(
                "DELETE FROM property_document WHERE id IN (%s, %s)",
                (str(ids["document_a"]), str(ids["document_b"])),
            )
            cursor.execute("DELETE FROM unit WHERE id = %s", (str(ids["unit"]),))
            cursor.execute(
                "DELETE FROM property WHERE id = %s", (str(ids["property"]),)
            )


def test_fact_search_projection_contains_label_value_and_source() -> None:
    projection = build_fact_search_text(
        subject_kind="UNIT",
        subject_label="Einheit 34",
        field_label="Miteigentumsanteil",
        display_value="47.732/1.000.000",
        source={"section": "§ 3", "quote": "Anteil laut Teilungserklärung"},
    )

    assert "Einheit 34" in projection
    assert "47.732/1.000.000" in projection
    assert "Miteigentumsanteil" in projection
    assert "Teilungserklärung" in projection


def test_rule_search_projection_is_stable_and_includes_effect_and_sources() -> None:
    projection = build_rule_search_text(
        topic="Heizkosten",
        title="Heizkostenverteilung",
        summary="Abrechnung nach Fläche und Verbrauch.",
        scope={"units": ["34", "12"], "building": "A"},
        effect={"verbrauch": "70 Prozent", "flaeche": "30 Prozent"},
        sources=[
            {"quote": "70 Prozent Verbrauch", "section": "§ 8"},
            {"quote": "30 Prozent Fläche", "section": "§ 8"},
        ],
    )

    assert "Heizkosten" in projection
    assert "30 Prozent Fläche" in projection
    assert "70 Prozent Verbrauch" in projection
    assert projection.index("12") < projection.index("34")


def test_search_projection_normalizes_and_bounds_source_quote() -> None:
    projection = build_fact_search_text(
        subject_kind="PROPERTY",
        subject_label="Haus A",
        field_label="Straße",
        display_value="Testweg 1",
        source={"quote": "  ".join(["Quelle"] * 600)},
    )

    assert "  " not in projection
    assert len(projection) <= 500 + len("PROPERTY Haus A Straße Testweg 1 ")


def test_knowledge_query_normalization_uses_nfkc_and_keeps_legal_separators() -> None:
    from app.services.property_knowledge_service import normalize_knowledge_query

    assert normalize_knowledge_query("  Heizkosten\u3000§ 8\t1／7  ") == "Heizkosten § 8 1/7"
    with pytest.raises(ValueError, match="blank"):
        normalize_knowledge_query(" \t\n ")


def test_knowledge_search_trigram_threshold_is_bounded() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    for value in ("0", "1"):
        assert Settings(declaration_search_trigram_threshold=value).declaration_search_trigram_threshold == Decimal(value)
    for value in ("-0.01", "1.01"):
        with pytest.raises(ValidationError):
            Settings(declaration_search_trigram_threshold=value)


@pytest.mark.parametrize("snapshot", [
    {"kind": "TEXT", "value": "50", "unit": "m²"},
    {"kind": "DECIMAL", "value": "50", "unit": "cm²"},
    {"kind": "DECIMAL", "value": "not-a-number", "unit": "m²"},
    {"kind": "DECIMAL", "value": "NaN", "unit": "m²"},
    {"kind": "DECIMAL", "value": "Infinity", "unit": "m²"},
])
def test_unit_decimal_snapshot_revalidation_fails_closed(snapshot) -> None:
    from app.services.property_knowledge_service import PropertyKnowledgeService

    assert not PropertyKnowledgeService._unit_decimal_snapshot_matches(
        snapshot, {"kind": "DECIMAL", "value": "50.00", "unit": "m²"}
    )


@pytest.mark.asyncio
async def test_knowledge_search_ranks_current_rules_and_keeps_conflicts_visible(
    approved_knowledge_graph,
) -> None:
    from app.core.db import get_tenant_session
    from app.services.property_knowledge_service import PropertyKnowledgeService

    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        heating = await service.search(approved_knowledge_graph.property_id, "Heizkosten", limit=20)
        allocation = await service.search(approved_knowledge_graph.property_id, "Verbrauch Fläche", limit=20)
        typo = await service.search(approved_knowledge_graph.property_id, "Aufzog 1/7", limit=20)
        conflict = await service.search(approved_knowledge_graph.property_id, "Wartelerähnliche Reparatur", limit=20)

    assert heating.hits[0].id == str(approved_knowledge_graph.heating_rule_id)
    assert allocation.hits[0].id == str(approved_knowledge_graph.heating_rule_id)
    assert typo.hits[0].id == str(approved_knowledge_graph.elevator_new_id)
    assert any(hit.status == "NEEDS_CLARIFICATION" for hit in conflict.hits)
    assert all(source.id.startswith(("fact-source:", "rule-source:")) for hit in heating.hits for source in hit.sources)


@pytest.mark.asyncio
async def test_knowledge_search_separates_history_and_revalidates_current_fingerprints(
    approved_knowledge_graph,
) -> None:
    from app.core.db import get_tenant_session
    from app.models.unit import Unit
    from app.services.property_knowledge_service import PropertyKnowledgeService

    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        unit_one = await session.scalar(
            select(Unit).where(Unit.id == approved_knowledge_graph.tie_unit_ids[0])
        )
        current = await service.search(approved_knowledge_graph.property_id, "Straße", limit=20)
        history = await service.search(approved_knowledge_graph.property_id, "Straße", limit=20, include_history=True)
        rules = await service.search(approved_knowledge_graph.property_id, "Aufzug", limit=20, include_history=True)
        opaque_assignment = await service.search(approved_knowledge_graph.property_id, "unit:2", limit=20)
        unit_area = await service.search(approved_knowledge_graph.property_id, "Wohnfläche", limit=20)
        unit_heated_area = await service.search(approved_knowledge_graph.property_id, "Beheizte Fläche", limit=20)
        legal = await service.search(approved_knowledge_graph.property_id, "Amtsgericht", limit=20)
        legal_details = await service.search(approved_knowledge_graph.property_id, "Akte 17", limit=20)
        relation = await service.search(approved_knowledge_graph.property_id, "AMENDS", limit=20)

    current_street = next(hit for hit in current.hits if hit.kind == "FACT")
    assert unit_one is not None
    assert unit_one.area == Decimal("50.00")
    assert unit_one.heated_area == Decimal("50.00")
    assert "Neue Straße 2" in current_street.summary
    assert all("Neue Straße 2" in source.quote for source in current_street.sources)
    assert any(hit.status == "SUPERSEDED" and "Alte Straße 1" in hit.summary for hit in history.hits)
    assert any(hit.id == str(approved_knowledge_graph.elevator_old_id) and hit.status == "SUPERSEDED" for hit in rules.hits)
    assert any(hit.effect["field_key"] == "area" for hit in unit_area.hits)
    assert any(hit.effect["field_key"] == "heated_area" for hit in unit_heated_area.hits)
    assert any(hit.topic == "legal_reference" for hit in legal.hits)
    assert any(hit.effect["field_key"] == "details" for hit in legal_details.hits)
    assert any(hit.topic == "document_relation" for hit in relation.hits)
    # unit_subject_key is intentionally not reconstructed from Assignment.unit_id:
    # the immutable approved snapshot is usable only after subject/property validation.
    assert any(hit.kind == "FACT" and hit.effect["field_key"] == "unit_subject_key" for hit in opaque_assignment.hits)


@pytest.mark.asyncio
async def test_knowledge_browse_is_tenant_first_clamps_limits_and_never_returns_foreign_data(
    approved_knowledge_graph,
) -> None:
    from app.core.db import get_tenant_session
    from app.services.property_knowledge_service import PropertyKnowledgeNotFound, PropertyKnowledgeService

    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        overview = await service.overview(approved_knowledge_graph.property_id)
        low = await service.search(approved_knowledge_graph.property_id, "Heizkosten", limit=-10)
        high = await service.search(approved_knowledge_graph.property_id, "Heizkosten", limit=500)
        for property_id in (
            approved_knowledge_graph.foreign_property_id,
            approved_knowledge_graph.unknown_property_id,
        ):
            with pytest.raises(PropertyKnowledgeNotFound):
                await service.search(property_id, "Heizkosten", limit=20)

    assert len(overview.documents) == 2
    assert [relation.status for relation in overview.document_relations] == ["EFFECTIVE"]
    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        history_overview = await PropertyKnowledgeService(
            session, approved_knowledge_graph.organization_id
        ).overview(approved_knowledge_graph.property_id, include_history=True)
    assert {relation.status for relation in history_overview.document_relations} == {
        "EFFECTIVE", "SUPERSEDED",
    }
    assert "repair" in overview.open_rule_topics
    assert low.limit == 1
    assert high.limit == 50


@pytest.mark.asyncio
async def test_knowledge_search_low_limit_is_deterministic_for_equal_scores(
    approved_knowledge_graph,
) -> None:
    from app.core.db import get_tenant_session
    from app.services.property_knowledge_service import PropertyKnowledgeService

    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        hits = [
            await service.search(approved_knowledge_graph.property_id, "Gleichstand", limit=1)
            for _ in range(3)
        ]

    assert all(len(response.hits) == 1 for response in hits)
    assert len({response.hits[0].id for response in hits}) == 1
    winner_id = min(map(str, approved_knowledge_graph.tie_unit_ids))
    winner_number = "1" if winner_id == str(approved_knowledge_graph.tie_unit_ids[0]) else "2"
    expected_id = uuid.uuid5(
        uuid.UUID("9d31bf6e-2df0-58ed-a9ec-30ecf7164431"),
        ":".join((
            str(approved_knowledge_graph.property_id), "UNIT", winner_id, "number",
            value_fingerprint({"kind": "TEXT", "value": winner_number}),
        )),
    )
    assert all(response.hits[0].id == str(expected_id) for response in hits)
    assert all(response.hits[0].summary == winner_number for response in hits)
    assert all(response.hits[0].effect["field_key"] == "number" for response in hits)


@pytest.mark.asyncio
async def test_knowledge_search_fails_closed_for_stale_decimal_and_archived_evidence(
    approved_knowledge_graph,
) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.db import get_tenant_session
    from app.models.property_document import PropertyDocument
    from app.models.unit import Unit
    from app.services.property_knowledge_service import PropertyKnowledgeService

    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        await session.execute(update(Unit).where(Unit.property_id == approved_knowledge_graph.property_id, Unit.number == "1").values(area=Decimal("60.00")))
        stale_area = await service.search(approved_knowledge_graph.property_id, "Wohnfläche", limit=20)
        await session.execute(update(PropertyDocument).where(PropertyDocument.property_id == approved_knowledge_graph.property_id, PropertyDocument.title == "Teilungserklärung").values(archived_at=datetime.now(timezone.utc)))
        overview = await service.overview(approved_knowledge_graph.property_id)
        archived_heating = await service.search(approved_knowledge_graph.property_id, "Heizkosten", limit=20)
        archived_relation = await service.search(approved_knowledge_graph.property_id, "AMENDS", limit=20)
        remaining = await service.search(approved_knowledge_graph.property_id, "Aufzug", limit=20)

    assert not any(hit.effect.get("field_key") == "area" for hit in stale_area.hits)
    assert [document.title for document in overview.documents] == ["Nachtrag"]
    assert overview.document_relations == []
    assert not archived_heating.hits
    assert not archived_relation.hits
    assert remaining.hits and all(source.document_title == "Nachtrag" for hit in remaining.hits for source in hit.sources)


@pytest.mark.asyncio
async def test_knowledge_search_rejects_corrupt_history_and_subject_status_mismatches(
    approved_knowledge_graph,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy import update

    from app.core.db import get_tenant_session
    from app.models.property_assignment import PropertyAssignment
    from app.models.property_document_relation import PropertyDocumentRelation
    from app.models.property_legal_reference import PropertyLegalReference
    from app.services.property_knowledge_service import PropertyKnowledgeService

    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        corrupt_service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        corrupt_service._repo.list_fact_sources = AsyncMock(return_value=(
            (SimpleNamespace(
                approved_value_json={"kind": "TEXT", "value": "Manipuliert"},
                value_fingerprint="0" * 64,
            ), "Teilungserklärung"),
        ))
        corrupt = await corrupt_service._candidate_to_hit(
            approved_knowledge_graph.property_id,
            {
                "kind": "FACT", "subject_kind": "PROPERTY",
                "subject_id": str(approved_knowledge_graph.property_id), "field_key": "street",
                "value_fingerprint": "0" * 64, "status": "SUPERSEDED", "score": 1.0,
            },
        )
    assert corrupt is None

    unknown_value = {"kind": "TEXT", "value": "unbekannt"}
    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        unknown_field_service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        unknown_field_service._repo.list_fact_sources = AsyncMock(return_value=(
            (SimpleNamespace(
                approved_value_json=unknown_value,
                value_fingerprint=value_fingerprint(unknown_value),
                subject_kind="LEGAL_REFERENCE",
                subject_id=approved_knowledge_graph.legal_id,
                field_key="unknown_field",
                status="EFFECTIVE",
            ), "Teilungserklärung"),
        ))
        unknown_field = await unknown_field_service._candidate_to_hit(
            approved_knowledge_graph.property_id,
            {
                "kind": "FACT", "subject_kind": "LEGAL_REFERENCE",
                "subject_id": str(approved_knowledge_graph.legal_id),
                "field_key": "unknown_field",
                "value_fingerprint": value_fingerprint(unknown_value),
                "status": "EFFECTIVE", "score": 1.0,
            },
        )
    assert unknown_field is None

    engine = create_async_engine(settings.database_url_migrator)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            async with session.begin():
                await session.execute(update(PropertyLegalReference).where(PropertyLegalReference.property_id == approved_knowledge_graph.property_id).values(status="SUPERSEDED"))
                await session.execute(update(PropertyAssignment).where(PropertyAssignment.property_id == approved_knowledge_graph.property_id).values(status="SUPERSEDED"))
                await session.execute(update(PropertyDocumentRelation).where(PropertyDocumentRelation.property_id == approved_knowledge_graph.property_id).values(status="SUPERSEDED"))
    finally:
        await engine.dispose()
    async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
        service = PropertyKnowledgeService(session, approved_knowledge_graph.organization_id)
        history = await service.search(approved_knowledge_graph.property_id, "Straße", limit=20, include_history=True)
        legal = await service.search(approved_knowledge_graph.property_id, "Amtsgericht", limit=20)
        assignment = await service.search(approved_knowledge_graph.property_id, "unit:2", limit=20)
        relation = await service.search(approved_knowledge_graph.property_id, "AMENDS", limit=20)

    assert any(hit.status == "SUPERSEDED" and "Alte Straße 1" in hit.summary for hit in history.hits)
    assert not legal.hits
    assert not assignment.hits
    assert not relation.hits


@pytest.mark.asyncio
async def test_approval_writes_searchable_fact_and_rule_projections(
    client_a_clerk, fully_reviewed_new_property_import,
) -> None:
    item = fully_reviewed_new_property_import.item
    response = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "knowledge-projection-new-0001"},
    )
    assert response.status_code == 200, response.json()
    property_id = uuid.UUID(response.json()["property_id"])

    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(item.organization_id)})
            mea_source = await session.scalar(select(ApprovedFactSource).where(
                ApprovedFactSource.property_id == property_id,
                ApprovedFactSource.subject_kind == "UNIT",
                ApprovedFactSource.field_key == "mea",
            ))
            assert mea_source is not None
            assert "Einheit" in mea_source.search_text
            assert "500.000/1.000.000" in mea_source.search_text
            assert "Miteigentumsanteil" in mea_source.search_text
            heating_rule = await session.scalar(select(PropertyRule).where(
                PropertyRule.property_id == property_id, PropertyRule.title == "Heizkosten",
            ))
            assert heating_rule is not None
            assert "Heizkosten" in heating_rule.search_text
            assert "Heizkosten werden verteilt." in heating_rule.search_text
            assert "allocation formula" in heating_rule.search_text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_approval_refreshes_current_fact_and_superseded_rule_projections(
    client_a_clerk, reviewed_existing_property_import,
) -> None:
    item = reviewed_existing_property_import.item
    response = await client_a_clerk.post(
        f"/api/declaration-imports/{item.id}/approve",
        json={"draft_revision": item.draft_revision, "idempotency_key": "knowledge-projection-existing-0001"},
    )
    assert response.status_code == 200, response.json()
    expected_fingerprint = value_fingerprint({"kind": "TEXT", "value": "Talstraße 112"})

    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            await session.execute(text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(item.organization_id)})
            sources = (await session.scalars(select(ApprovedFactSource).where(
                ApprovedFactSource.property_id == reviewed_existing_property_import.property.id,
                ApprovedFactSource.field_key == "street",
            ))).all()
            current = [source for source in sources if source.status == "EFFECTIVE" and source.value_fingerprint == expected_fingerprint]
            assert current
            assert all("Talstraße 112" in source.search_text for source in current)
            assert any(source.status == "SUPERSEDED" for source in sources)
            rules = (await session.scalars(select(PropertyRule).where(
                PropertyRule.property_id == reviewed_existing_property_import.property.id,
            ))).all()
            old_rule = next(rule for rule in rules if rule.id == reviewed_existing_property_import.old_rule.id)
            new_rule = next(rule for rule in rules if rule.status == "EFFECTIVE")
            assert old_rule.status == "SUPERSEDED"
            assert "Altbestand § 8" in old_rule.search_text
            assert "Nachtrag § 8" in new_rule.search_text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_rw_rule_source_refresh_updates_vector_but_cannot_mutate_canonical_rule(
    app_rw_session, reviewed_existing_property_import,
) -> None:
    from app.repositories.property_knowledge_repo import PropertyKnowledgeRepository

    property_row = reviewed_existing_property_import.property
    old_rule = reviewed_existing_property_import.old_rule
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(property_row.organization_id)},
    )
    await app_rw_session.execute(
        text(
            "INSERT INTO property_rule_source "
            "(id, organization_id, property_id, property_rule_id, property_document_id, pdf_page, section, quote) "
            "VALUES (:id, :org, :property, :rule, :document, 2, '§ 8', 'RefreshSonderbegriff')"
        ),
        {
            "id": uuid.uuid4(), "org": property_row.organization_id, "property": property_row.id,
            "rule": old_rule.id, "document": reviewed_existing_property_import.main.id,
        },
    )
    repository = PropertyKnowledgeRepository(app_rw_session, property_row.organization_id)
    await repository._refresh_rule_search_text(old_rule.id)
    await app_rw_session.flush()
    matches = await app_rw_session.scalar(
        text(
            "SELECT search_vector @@ websearch_to_tsquery('german', 'RefreshSonderbegriff') "
            "FROM property_rule WHERE id = :id"
        ),
        {"id": old_rule.id},
    )
    assert matches is True
    for statement in (
        "UPDATE property_rule SET title = 'Verboten' WHERE id = :id",
        "UPDATE property_rule SET scope_json = '{\"all_units\": false}'::jsonb WHERE id = :id",
    ):
        with pytest.raises(Exception):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(statement), {"id": old_rule.id})
    await app_rw_session.rollback()


@pytest.mark.asyncio
async def test_search_schema_has_indexes_and_restricted_query_run_privileges(
    migrator_session,
) -> None:
    columns = await migrator_session.execute(
        text(
            "SELECT table_name, column_name, is_generated "
            "FROM information_schema.columns "
            "WHERE (table_name = 'approved_fact_source' AND column_name IN ('search_text', 'search_vector')) "
            "OR (table_name = 'property_rule' AND column_name = 'search_vector')"
        )
    )
    assert {
        (row.table_name, row.column_name, row.is_generated)
        for row in columns
    } == {
        ("approved_fact_source", "search_text", "NEVER"),
        ("approved_fact_source", "search_vector", "ALWAYS"),
        ("property_rule", "search_vector", "ALWAYS"),
    }

    indexes = await migrator_session.execute(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename IN "
            "('approved_fact_source', 'property_rule')"
        )
    )
    assert {
        "ix_approved_fact_source_search_vector",
        "ix_property_rule_search_vector",
        "ix_approved_fact_source_search_trgm",
        "ix_property_rule_search_trgm",
    } <= {row.indexname for row in indexes}

    extension = await migrator_session.scalar(
        text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    )
    assert extension == 1

    historical_rule_rows = await migrator_session.execute(
        text(
            "SELECT count(*), bool_and(btrim(search_text) <> '') "
            "FROM approved_fact_source WHERE subject_kind = 'RULE' AND field_key = 'rule'"
        )
    )
    count, all_projected = historical_rule_rows.one()
    assert count == 0 or all_projected is True

    rls = await migrator_session.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE relname = 'property_knowledge_query_run'"
        )
    )
    assert tuple(rls.one()) == (True, True)
    policies = await migrator_session.execute(
        text("SELECT cmd FROM pg_policies WHERE tablename = 'property_knowledge_query_run'")
    )
    assert {row.cmd for row in policies} == {"SELECT", "INSERT", "UPDATE"}
    privileges = await migrator_session.execute(
        text(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = 'property_knowledge_query_run' AND grantee = 'app_rw'"
        )
    )
    assert {row.privilege_type for row in privileges} == {"SELECT", "INSERT", "UPDATE"}


@pytest.mark.asyncio
async def test_query_run_lifecycle_rejects_rewriting_sanitized_reservation(
    migrator_session, seed_data,
) -> None:
    organization_id = seed_data["org_a_id"]
    session = migrator_session
    await session.execute(text("SELECT set_config('app.current_org_id', :org, true), set_config('app.current_user_id', :actor, true)"), {"org": str(organization_id), "actor": str(seed_data["user_a_admin_id"])})
    property_id = uuid.uuid4()
    await migrator_session.execute(
        text(
            "INSERT INTO property (id, organization_id, name, street, zip_code, city, management_type) "
            "VALUES (:id, :organization_id, 'Test', 'Testweg 1', '12345', 'Teststadt', 'WEG')"
        ),
        {"id": property_id, "organization_id": organization_id},
    )
    row_id = uuid.uuid4()
    await migrator_session.execute(
        text(
            "INSERT INTO property_knowledge_query_run "
            "(id, organization_id, initiated_by, property_id, budget_window_start, provider, model, reserved_cost_usd, reservation_expires_at) "
            "VALUES (:id, :organization_id, current_setting('app.current_user_id')::uuid, :property_id, now(), 'fake', 'test', 0.01, now() + interval '1 hour')"
        ),
        {"id": row_id, "organization_id": organization_id, "property_id": property_id},
    )
    with pytest.raises(Exception, match="immutable"):
        async with migrator_session.begin_nested():
            await migrator_session.execute(
                text("UPDATE property_knowledge_query_run SET provider = 'other' WHERE id = :id"),
                {"id": row_id},
            )

    await migrator_session.execute(text("DELETE FROM property_knowledge_query_run WHERE id=:id"), {"id": row_id})


@pytest.mark.asyncio
async def test_app_rw_query_run_reservations_require_reserved_lifecycle(
    app_rw_session, seed_data,
) -> None:
    organization_id = seed_data["org_a_id"]
    await app_rw_session.execute(text("SELECT set_config('app.current_user_id', :actor, true)"), {"actor": str(seed_data["user_a_admin_id"])})
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(organization_id)},
    )
    property_id = uuid.uuid4()
    await app_rw_session.execute(
        text(
            "INSERT INTO property (id, organization_id, name, street, zip_code, city, management_type) "
            "VALUES (:id, :organization_id, 'Ledger', 'Testweg 1', '12345', 'Teststadt', 'WEG')"
        ),
        {"id": property_id, "organization_id": organization_id},
    )
    reservation_id = uuid.uuid4()
    insert = text(
        "INSERT INTO property_knowledge_query_run "
        "(id, organization_id, initiated_by, property_id, budget_window_start, status, provider, model, reserved_cost_usd, reservation_expires_at) "
        "VALUES (:id, :organization_id, current_setting('app.current_user_id')::uuid, :property_id, now(), :status, 'fake', 'test', 0.01, now() + interval '1 hour')"
    )
    for status in ("FINALIZED", "EXPIRED"):
        with pytest.raises(Exception, match="RESERVED"):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(insert, {
                    "id": uuid.uuid4(), "organization_id": organization_id,
                    "property_id": property_id, "status": status,
                })
    await app_rw_session.execute(insert, {
        "id": reservation_id, "organization_id": organization_id,
        "property_id": property_id, "status": "RESERVED",
    })
    await app_rw_session.execute(
        text(
            "UPDATE property_knowledge_query_run SET status = 'FINALIZED', finalized_at = now(), "
            "outcome = 'ANSWER', input_tokens = 1 WHERE id = :id"
        ),
        {"id": reservation_id},
    )
    with pytest.raises(Exception, match="RESERVED"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(
                text("UPDATE property_knowledge_query_run SET outcome = 'OTHER' WHERE id = :id"),
                {"id": reservation_id},
            )
    with pytest.raises(Exception):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(insert, {
                "id": uuid.uuid4(), "organization_id": seed_data["org_b_id"],
                "property_id": property_id, "status": "RESERVED",
            })
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(seed_data["org_b_id"])},
    )
    foreign_visible = await app_rw_session.scalar(
        text("SELECT count(*) FROM property_knowledge_query_run WHERE id = :id"),
        {"id": reservation_id},
    )
    assert foreign_visible == 0
    await app_rw_session.rollback()


def test_0026_downgrade_removes_only_knowledge_search_schema_and_reupgrades(
    migration_config_at_head,
) -> None:
    config = migration_config_at_head
    command.downgrade(config, "0025")
    connection = psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"])
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT to_regclass('public.property_knowledge_query_run')")
        assert cursor.fetchone() == (None,)
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'approved_fact_source' AND column_name IN ('search_text', 'search_vector')"
        )
        assert cursor.fetchall() == []
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'property_rule' AND column_name = 'search_vector'"
        )
        assert cursor.fetchall() == []
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE indexname IN "
            "('ix_approved_fact_source_search_vector', 'ix_property_rule_search_vector', "
            "'ix_approved_fact_source_search_trgm', 'ix_property_rule_search_trgm')"
        )
        assert cursor.fetchall() == []
        cursor.execute(
            "SELECT to_regprocedure('guard_property_knowledge_query_run_insert()'), "
            "to_regprocedure('guard_property_knowledge_query_run_update()')"
        )
        assert cursor.fetchone() == (None, None)
        cursor.execute(
            "SELECT count(*) FROM pg_policies "
            "WHERE tablename = 'property_knowledge_query_run'"
        )
        assert cursor.fetchone() == (0,)
    finally:
        connection.close()


def test_0026_backfills_every_supported_0025_fact_kind_including_rule(
    migration_config_at_head,
) -> None:
    config = migration_config_at_head
    command.downgrade(config, "0025")
    connection = psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"])
    ids = {name: uuid.uuid4() for name in (
        "property", "unit", "document_a", "document_b", "import", "legal", "assignment", "relation", "rule", "rule_source",
    )}
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT id, organization_id FROM "user" ORDER BY created_at LIMIT 1')
        user_id, organization_id = cursor.fetchone()
        cursor.execute(
            "INSERT INTO property (id, organization_id, name, street, zip_code, city, management_type) "
            "VALUES (%s, %s, 'Backfill Haus', 'Testweg 1', '12345', 'Teststadt', 'WEG')",
            (str(ids["property"]), str(organization_id)),
        )
        cursor.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, mea) "
            "VALUES (%s, %s, %s, '34', 'WOHNUNG', 47732)",
            (str(ids["unit"]), str(organization_id), str(ids["property"])),
        )
        for key, title, digest in (("document_a", "Alturkunde", "e" * 64), ("document_b", "Nachtrag", "f" * 64)):
            cursor.execute(
                "INSERT INTO property_document (id, organization_id, property_id, doc_type, title, original_filename, mime_type, content_byte_size, content_sha256, storage_uri, storage_backend, page_count, uploaded_by) "
                "VALUES (%s, %s, %s, 'DECLARATION', %s, %s, 'application/pdf', 1, %s, %s, 'LOCAL', 1, %s)",
                (str(ids[key]), str(organization_id), str(ids["property"]), title, f"{key}.pdf", digest, f"test/{key}.pdf", str(user_id)),
            )
        cursor.execute(
            "INSERT INTO declaration_import (id, organization_id, mode, target_property_id, status, initiated_by, max_cost_usd) "
            "VALUES (%s, %s, 'EXISTING_PROPERTY', %s, 'DRAFT', %s, 1)",
            (str(ids["import"]), str(organization_id), str(ids["property"]), str(user_id)),
        )
        cursor.execute(
            "INSERT INTO property_legal_reference (id, organization_id, property_id, reference_type, details_json, source_import_id) "
            "VALUES (%s, %s, %s, 'LAND_REGISTER', '{}'::jsonb, %s)",
            (str(ids["legal"]), str(organization_id), str(ids["property"]), str(ids["import"])),
        )
        cursor.execute(
            "INSERT INTO property_assignment (id, organization_id, property_id, unit_id, stable_key, assignment_type, designation, details_json, source_import_id) "
            "VALUES (%s, %s, %s, %s, 'parking', 'SPECIAL_USE', 'Stellplatz 2', '{}'::jsonb, %s)",
            (str(ids["assignment"]), str(organization_id), str(ids["property"]), str(ids["unit"]), str(ids["import"])),
        )
        cursor.execute(
            "INSERT INTO property_document_relation (id, organization_id, property_id, source_document_id, target_document_id, relation_type, source_import_id) "
            "VALUES (%s, %s, %s, %s, %s, 'AMENDS', %s)",
            (str(ids["relation"]), str(organization_id), str(ids["property"]), str(ids["document_b"]), str(ids["document_a"]), str(ids["import"])),
        )
        cursor.execute(
            "INSERT INTO property_rule (id, organization_id, property_id, stable_key, topic, title, summary, scope_json, effect_json, source_import_id, approved_by, approved_at) "
            "VALUES (%s, %s, %s, 'heating', 'heating_costs', 'Heizkosten', '70 Prozent Verbrauch', '{}'::jsonb, '{}'::jsonb, %s, %s, now())",
            (str(ids["rule"]), str(organization_id), str(ids["property"]), str(ids["import"]), str(user_id)),
        )
        cursor.execute(
            "INSERT INTO property_rule_source (id, organization_id, property_id, property_rule_id, property_document_id, pdf_page, section, quote) "
            "VALUES (%s, %s, %s, %s, %s, 1, '§ 8', 'EindeutigerQuellbegriff für den Backfill')",
            (str(ids["rule_source"]), str(organization_id), str(ids["property"]), str(ids["rule"]), str(ids["document_a"])),
        )
        rows = (
            ("PROPERTY", ids["property"], "city", {"kind": "TEXT", "value": "Teststadt"}),
            ("UNIT", ids["unit"], "mea", {"kind": "MEA", "numerator": 47732, "denominator": 1000000}),
            ("LEGAL_REFERENCE", ids["legal"], "district", {"kind": "TEXT", "value": "Düsseldorf"}),
            ("ASSIGNMENT", ids["assignment"], "designation", {"kind": "TEXT", "value": "Stellplatz 2"}),
            ("DOCUMENT_RELATION", ids["relation"], "relation", {"relation_type": "AMENDS"}),
            ("RULE", ids["rule"], "rule", {"title": "Heizkosten"}),
        )
        for kind, subject_id, field_key, value in rows:
            cursor.execute(
                "INSERT INTO approved_fact_source (id, organization_id, property_id, subject_kind, subject_id, field_key, approved_value_json, value_fingerprint, valid_from, property_document_id, pdf_page, quote, source_import_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, now(), %s, 1, 'Historischer Beleg', %s)",
                (str(uuid.uuid4()), str(organization_id), str(ids["property"]), kind, str(subject_id), field_key, json.dumps(value), "a" * 64, str(ids["document_a"]), str(ids["import"])),
            )
        connection.commit()
        command.upgrade(config, "head")
        cursor.execute(
            "SELECT subject_kind, search_text FROM approved_fact_source WHERE source_import_id = %s ORDER BY subject_kind",
            (str(ids["import"]),),
        )
        projections = dict(cursor.fetchall())
        assert set(projections) == {"PROPERTY", "UNIT", "LEGAL_REFERENCE", "ASSIGNMENT", "DOCUMENT_RELATION", "RULE"}
        assert "47.732/1.000.000" in projections["UNIT"]
        assert "Heizkosten" in projections["RULE"]
        assert all("Historischer Beleg" in projection for projection in projections.values())
        cursor.execute(
            "SELECT search_text, search_vector @@ websearch_to_tsquery('german', 'EindeutigerQuellbegriff') "
            "FROM property_rule WHERE id = %s",
            (str(ids["rule"]),),
        )
        rule_search_text, rule_matches = cursor.fetchone()
        assert "EindeutigerQuellbegriff" in rule_search_text
        assert rule_matches is True
    finally:
        connection.close()
        try:
            command.upgrade(config, "head")
        finally:
            _delete_backfill_graph(ids)


@pytest.mark.asyncio
async def test_property_knowledge_api_is_readable_for_all_authenticated_roles_and_hides_other_tenants(
    approved_knowledge_graph,
    client_a_admin,
    client_a_clerk,
    client_a_readonly,
    client_unauth,
) -> None:
    property_id = approved_knowledge_graph.property_id
    routes = (
        f"/api/properties/{property_id}/rules",
        f"/api/properties/{property_id}/knowledge",
        f"/api/properties/{property_id}/knowledge/search?q=Heizkosten",
    )

    for client in (client_a_admin, client_a_clerk, client_a_readonly):
        for route in routes:
            response = await client.get(route)
            assert response.status_code == 200, response.text

    for route in routes:
        assert (await client_unauth.get(route)).status_code == 401

    for hidden_property_id in (
        approved_knowledge_graph.foreign_property_id,
        approved_knowledge_graph.unknown_property_id,
    ):
        hidden_routes = (
            f"/api/properties/{hidden_property_id}/rules",
            f"/api/properties/{hidden_property_id}/knowledge",
            f"/api/properties/{hidden_property_id}/knowledge/search?q=Heizkosten",
        )
        responses = [await client_a_admin.get(route) for route in hidden_routes]
        assert [response.status_code for response in responses] == [404, 404, 404]
        assert len({response.json()["detail"] for response in responses}) == 1


@pytest.mark.asyncio
async def test_property_knowledge_api_filters_rule_history_and_validates_search_parameters(
    approved_knowledge_graph,
    client_a_admin,
) -> None:
    property_id = approved_knowledge_graph.property_id
    rules_url = f"/api/properties/{property_id}/rules"
    knowledge_url = f"/api/properties/{property_id}/knowledge"
    search_url = f"/api/properties/{property_id}/knowledge/search"

    default_rules = await client_a_admin.get(rules_url)
    history_rules = await client_a_admin.get(
        rules_url,
        params=[("status", "EFFECTIVE"), ("status", "SUPERSEDED")],
    )
    all_history_rules = await client_a_admin.get(rules_url, params={"include_history": "true"})
    assert default_rules.status_code == history_rules.status_code == all_history_rules.status_code == 200
    assert [
        (rule["topic"], rule["title"], rule["id"])
        for rule in default_rules.json()
    ] == sorted(
        (rule["topic"], rule["title"], rule["id"])
        for rule in default_rules.json()
    )
    assert all(rule["status"] != "SUPERSEDED" for rule in default_rules.json())
    assert {rule["status"] for rule in history_rules.json()} == {"EFFECTIVE", "SUPERSEDED"}
    assert any(rule["status"] == "SUPERSEDED" for rule in all_history_rules.json())
    assert any(rule["sources"] for rule in all_history_rules.json())
    assert all(source["id"].startswith("rule-source:") for rule in all_history_rules.json() for source in rule["sources"])
    assert (await client_a_admin.get(rules_url, params={"status": "DRAFT"})).status_code == 422

    default_knowledge = await client_a_admin.get(knowledge_url)
    history_knowledge = await client_a_admin.get(knowledge_url, params={"include_history": "true"})
    assert default_knowledge.status_code == history_knowledge.status_code == 200
    assert all(
        rule["status"] != "SUPERSEDED"
        for rules in default_knowledge.json()["rules_by_topic"].values()
        for rule in rules
    )
    assert any(
        rule["status"] == "SUPERSEDED"
        for rules in history_knowledge.json()["rules_by_topic"].values()
        for rule in rules
    )

    normalized = await client_a_admin.get(search_url, params={"q": "  Heizkosten\u3000"})
    assert normalized.status_code == 200
    assert normalized.json()["normalized_query"] == "Heizkosten"
    assert normalized.json()["limit"] == 20
    assert normalized.json()["include_history"] is False
    assert all(hit["status"] != "SUPERSEDED" for hit in normalized.json()["hits"])
    default_facts = await client_a_admin.get(search_url, params={"q": "Straße"})
    history_facts = await client_a_admin.get(search_url, params={"q": "Straße", "include_history": "true"})
    assert default_facts.status_code == history_facts.status_code == 200
    assert all(hit["status"] != "SUPERSEDED" for hit in default_facts.json()["hits"])
    assert any(hit["status"] == "SUPERSEDED" for hit in history_facts.json()["hits"])
    history_search = await client_a_admin.get(search_url, params={"q": "Aufzug", "include_history": "true"})
    assert history_search.status_code == 200
    assert any(hit["status"] == "SUPERSEDED" for hit in history_search.json()["hits"])
    assert all(source["id"].startswith(("fact-source:", "rule-source:")) for hit in history_search.json()["hits"] for source in hit["sources"])
    for params in ({}, {"q": "  \t "}, {"q": "Heizkosten", "limit": 0}, {"q": "Heizkosten", "limit": 51}):
        assert (await client_a_admin.get(search_url, params=params)).status_code == 422


@pytest.mark.asyncio
async def test_property_knowledge_api_never_exposes_unreviewed_candidate_data(
    approved_knowledge_graph,
    client_a_admin,
) -> None:
    from app.core.db import get_tenant_session
    from app.models.declaration_candidate import DeclarationCandidate
    from sqlalchemy import delete

    draft_token = "UNGEPRUEFTER-ENTWURF-HEIZKOSTEN"
    candidate_id = uuid.uuid4()
    try:
        async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
            source_import_id = await session.scalar(
                select(PropertyRule.source_import_id).where(
                    PropertyRule.id == approved_knowledge_graph.heating_rule_id,
                )
            )
            assert source_import_id is not None
            session.add(DeclarationCandidate(
                id=candidate_id,
                organization_id=approved_knowledge_graph.organization_id,
                import_id=source_import_id,
                stable_key="candidate:unreviewed-knowledge-route",
                candidate_type="PROPERTY_FIELD",
                subject_key=f"property:{approved_knowledge_graph.property_id}",
                field_key="name",
                operation="UPDATE",
                proposed_value_json={"kind": "TEXT", "value": draft_token},
                confidence=Decimal("0.9900"),
                risk_class="LOW",
            ))

        property_id = approved_knowledge_graph.property_id
        responses = (
            await client_a_admin.get(f"/api/properties/{property_id}/rules"),
            await client_a_admin.get(f"/api/properties/{property_id}/knowledge"),
            await client_a_admin.get(f"/api/properties/{property_id}/knowledge/search", params={"q": draft_token}),
        )
        assert all(response.status_code == 200 for response in responses)
        assert all(draft_token not in response.text for response in responses[:2])
        assert responses[2].json()["hits"] == []
    finally:
        async with get_tenant_session(approved_knowledge_graph.organization_id) as session:
            await session.execute(delete(DeclarationCandidate).where(DeclarationCandidate.id == candidate_id))
