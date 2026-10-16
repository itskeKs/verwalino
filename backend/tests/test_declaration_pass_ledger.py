"""Durable, content-free provider pass ledger regression tests."""

from __future__ import annotations

from decimal import Decimal
import json
import uuid

import pytest

from tests.legacy_migration_database import legacy_auth_database as legacy_0028_database
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.declarations.observability import PassLedgerEntry, ProviderPassMetadata
from app.declarations.pipeline import DeclarationPipeline, PipelineFailure, _Ledger
from app.declarations.provider import ProviderFailure, ProviderReservationEstimate, ProviderUsage
from app.core.config import settings
from app.core.db import get_tenant_session, get_worker_tenant_session
from app.models.declaration_import import (
    DeclarationExtractionRun,
    DeclarationImport,
    DeclarationImportPass,
)
from app.repositories.declaration_import_repo import (
    DeclarationImportRepository,
    PassLedgerEntry as RepositoryPassLedgerEntry,
)


def _entry(*, attempt_no: int = 1) -> PassLedgerEntry:
    metadata = ProviderPassMetadata.from_values(
        pass_id="inventory",
        attempt_no=attempt_no,
        stage="provider",
        configured_model="gpt-5.6-luna",
        returned_model_id="gpt-5.6-luna",
        reasoning_effort="medium",
        request_id=None,
        prompt_version="test-v1",
        prompt_sha256="0" * 64,
        schema_version="1",
        schema_sha256="1" * 64,
        harness_version="1",
        harness_sha256="2" * 64,
        input_tokens=1,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        output_tokens=1,
        estimated_cost_usd=Decimal("0.000001"),
        latency_ms=1,
        retry_count=attempt_no - 1,
        route_action="NONE",
        route_reason_codes=(),
        failure_class=None,
    )
    return PassLedgerEntry.from_metadata(metadata)


def test_pass_ledger_model_and_repository_expose_typed_append_only_seam() -> None:
    assert DeclarationImportPass.__tablename__ == "declaration_import_pass"
    assert RepositoryPassLedgerEntry is PassLedgerEntry
    assert "safe_metadata" in DeclarationImportPass.__table__.c


@pytest.mark.asyncio
async def test_pass_ledger_schema_is_rls_force_and_append_only(legacy_0028_database, migrator_session) -> None:
    table = "declaration_import_pass"
    rls = await migrator_session.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = :table"
        ),
        {"table": table},
    )
    row = rls.one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True

    policies = await migrator_session.execute(
        text("SELECT cmd FROM pg_policies WHERE tablename = :table"), {"table": table}
    )
    assert {row.cmd for row in policies} == {"SELECT", "INSERT"}

    privileges = await migrator_session.execute(
        text(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = :table AND grantee = 'app_rw'"
        ),
        {"table": table},
    )
    assert {row.privilege_type for row in privileges} == {"SELECT", "INSERT"}

    constraints = await migrator_session.execute(
        text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:table AS regclass)"),
        {"table": table},
    )
    names = {row.conname for row in constraints}
    assert "uq_declaration_import_pass_run_attempt" in names
    assert "fk_declaration_import_pass_run_tenant" in names
    run_constraints = await migrator_session.execute(
        text("SELECT conname FROM pg_constraint WHERE conrelid = 'declaration_extraction_run'::regclass")
    )
    assert "uq_declaration_extraction_run_organization_id_id" in {
        row.conname for row in run_constraints
    }


@pytest.mark.asyncio
async def test_pass_ledger_append_uses_safe_metadata_and_global_attempt_numbers(
    seed_data, migrator_session
) -> None:
    organization_id = seed_data["org_a_id"]
    import_id = uuid.uuid4()
    run_id = uuid.uuid4()
    await migrator_session.execute(
        text(
            "INSERT INTO declaration_import "
            "(id, organization_id, mode, status, initiated_by, max_cost_usd) "
            "VALUES (:id, :org, 'NEW_PROPERTY', 'PREFLIGHT', :user_id, 1)"
        ),
        {"id": import_id, "org": organization_id, "user_id": seed_data["user_a_admin_id"]},
    )
    await migrator_session.execute(
        text(
            "INSERT INTO declaration_extraction_run "
            "(id, organization_id, import_id, provider, model_name, prompt_version, schema_version, harness_version) "
            "VALUES (:id, :org, :import_id, 'fake', 'test', 'test', 'test', 'test')"
        ),
        {"id": run_id, "org": organization_id, "import_id": import_id},
    )

    repository = DeclarationImportRepository(migrator_session, organization_id)
    rows = await repository.append_pass_entries(run_id, (_entry(), _entry(attempt_no=2)))
    assert [row.attempt_no for row in rows] == [1, 2]
    assert [row.safe_metadata["attempt_no"] for row in rows] == [1, 2]
    assert set(rows[0].safe_metadata) == set(_entry().as_safe_dict())
    assert "quote" not in rows[0].safe_metadata
    assert "tenant_id" not in rows[0].safe_metadata


@pytest.mark.asyncio
async def test_pass_ledger_rejects_untyped_or_duplicate_append(seed_data, migrator_session) -> None:
    repository = DeclarationImportRepository(migrator_session, seed_data["org_a_id"])
    with pytest.raises(ValueError, match="PassLedgerEntry"):
        await repository.append_pass_entries(uuid.uuid4(), ({"prompt": "private"},))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pipeline_checkpoint_persists_one_content_free_row_per_sdk_attempt(
    seed_data,
) -> None:
    # Reuse the established real pipeline fixture: this exercises the durable
    # checkpoint boundary, not a mocked repository implementation.
    from tests.declaration_fixtures import make_text_pdf
    from tests.test_declaration_pipeline import _RecordingProvider, _seed_pipeline_import

    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    await DeclarationPipeline(_RecordingProvider(document_key), store=store).run(lease)

    async with get_tenant_session(lease.organization_id) as session:
        run = await session.scalar(
            select(DeclarationExtractionRun)
            .where(DeclarationExtractionRun.import_id == lease.import_id)
        )
        assert run is not None
        rows = (
            await session.scalars(
                select(DeclarationImportPass)
                .where(DeclarationImportPass.declaration_extraction_run_id == run.id)
                .order_by(DeclarationImportPass.attempt_no)
            )
        ).all()

    assert rows
    assert [row.attempt_no for row in rows] == list(range(1, len(rows) + 1))
    assert all(row.safe_metadata["attempt_no"] == row.attempt_no for row in rows)
    forbidden = {"quote", "prompt", "document_key", "image_data_url", "response_body", "tenant_id", "path"}
    assert all(not (forbidden & set(row.safe_metadata)) for row in rows)


@pytest.mark.asyncio
async def test_two_attempt_provider_failure_persists_before_retry_wait_without_duplicates(
    seed_data,
) -> None:
    from tests.declaration_fixtures import make_text_pdf
    from tests.test_declaration_pipeline import _seed_pipeline_import

    class TwoAttemptTimeoutProvider:
        def estimate_reservation(self, *_args, **_kwargs):
            return ProviderReservationEstimate(26, 2, (13, 13))

        async def inventory(self, *_args, **_kwargs):
            usage = ProviderUsage(26, 14, Decimal("0.000002"), 2)
            first = ProviderPassMetadata.from_values(
                **(_entry().as_safe_dict() | {
                    "attempt_no": 1,
                    "stage": "provider",
                    "input_tokens": 13,
                    "output_tokens": 7,
                    "estimated_cost_usd": Decimal("0.000001"),
                    "failure_class": "PROVIDER_SCHEMA_FAILURE",
                    "route_action": "HUMAN_REVIEW",
                    "route_reason_codes": ("PROVIDER_SCHEMA_FAILURE",),
                })
            )
            second = ProviderPassMetadata.from_values(
                **(_entry().as_safe_dict() | {
                    "attempt_no": 2,
                    "stage": "schema_repair",
                    "input_tokens": 13,
                    "output_tokens": 7,
                    "estimated_cost_usd": Decimal("0.000001"),
                    "retry_count": 1,
                    "failure_class": "PROVIDER_TIMEOUT",
                    "route_action": "HUMAN_REVIEW",
                    "route_reason_codes": ("PROVIDER_TIMEOUT",),
                })
            )
            raise ProviderFailure(
                "PROVIDER_TIMEOUT",
                True,
                "safe",
                usage=usage,
                attempt_metadata=(first, second),
                metadata=second,
            )

    lease, _document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    await DeclarationPipeline(TwoAttemptTimeoutProvider(), store=store).run(lease)

    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        run = await session.scalar(
            select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == lease.import_id)
        )
        assert run is not None
        rows = (
            await session.scalars(
                select(DeclarationImportPass)
                .where(DeclarationImportPass.declaration_extraction_run_id == run.id)
                .order_by(DeclarationImportPass.attempt_no)
            )
        ).all()

    assert item is not None and item.status == "RETRY_WAIT"
    assert [row.attempt_no for row in rows] == [1, 2]
    assert [row.safe_metadata["stage"] for row in rows] == ["provider", "schema_repair"]
    assert [row.safe_metadata["failure_class"] for row in rows] == [
        "PROVIDER_SCHEMA_FAILURE",
        "PROVIDER_TIMEOUT",
    ]


async def _pipeline_with_running_run(seed_data):
    from tests.declaration_fixtures import make_text_pdf
    from tests.test_declaration_pipeline import _seed_pipeline_import

    lease, _document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(
            select(DeclarationImport).where(DeclarationImport.id == lease.import_id)
        )
        assert item is not None
        item.status = "PREFLIGHT"
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings
    pipeline._provider = object()
    pipeline._tenant_session_factory = get_worker_tenant_session
    pipeline._active_lease = lease
    pipeline._active_run_id = await pipeline._start_run(lease)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    return pipeline, lease, ledger


@pytest.mark.asyncio
async def test_checkpoint_renumbers_two_schema_repair_attempts_globally(seed_data) -> None:
    pipeline, lease, ledger = await _pipeline_with_running_run(seed_data)
    ledger.pass_entries.extend((_entry(), _entry(attempt_no=2)))

    await pipeline._checkpoint(ledger)

    assert ledger.pass_entries == []
    async with get_tenant_session(lease.organization_id) as session:
        rows = (
            await session.scalars(
                select(DeclarationImportPass)
                .where(
                    DeclarationImportPass.declaration_extraction_run_id
                    == pipeline._active_run_id
                )
                .order_by(DeclarationImportPass.attempt_no)
            )
        ).all()
    assert [row.attempt_no for row in rows] == [1, 2]
    assert [row.safe_metadata["attempt_no"] for row in rows] == [1, 2]


@pytest.mark.asyncio
async def test_failure_persists_pending_attempt_before_retry_wait(seed_data) -> None:
    pipeline, lease, ledger = await _pipeline_with_running_run(seed_data)
    ledger.pass_entries.append(_entry())

    await pipeline._fail(lease, "PROVIDER_TIMEOUT", retryable=True, ledger=ledger)

    assert ledger.pass_entries == []
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        rows = (
            await session.scalars(
                select(DeclarationImportPass).where(
                    DeclarationImportPass.declaration_extraction_run_id
                    == pipeline._active_run_id
                )
            )
        ).all()
    assert item is not None and item.status == "RETRY_WAIT"
    assert len(rows) == 1 and rows[0].safe_metadata["attempt_no"] == 1


@pytest.mark.asyncio
async def test_failure_with_missing_active_run_keeps_queue_and_import_unchanged(seed_data) -> None:
    pipeline, lease, ledger = await _pipeline_with_running_run(seed_data)
    ledger.pass_entries.append(_entry())
    pipeline._active_run_id = uuid.uuid4()

    with pytest.raises(PipelineFailure, match="EXTRACTION_RUN_NOT_FOUND"):
        await pipeline._fail(lease, "PROVIDER_TIMEOUT", retryable=True, ledger=ledger)

    assert len(ledger.pass_entries) == 1
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert item is not None and item.status == "PREFLIGHT"


@pytest.mark.asyncio
async def test_checkpoint_rollback_retains_queue_and_retry_writes_no_duplicate(
    seed_data, monkeypatch
) -> None:
    pipeline, lease, ledger = await _pipeline_with_running_run(seed_data)
    ledger.pass_entries.append(_entry())
    original_transition = DeclarationImportRepository.transition_with_lease

    async def reject_after_insert(self, *args, **kwargs):
        return False

    monkeypatch.setattr(DeclarationImportRepository, "transition_with_lease", reject_after_insert)
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._checkpoint(ledger)
    assert len(ledger.pass_entries) == 1

    monkeypatch.setattr(
        DeclarationImportRepository, "transition_with_lease", original_transition
    )
    await pipeline._checkpoint(ledger)
    assert ledger.pass_entries == []
    async with get_tenant_session(lease.organization_id) as session:
        rows = (
            await session.scalars(
                select(DeclarationImportPass).where(
                    DeclarationImportPass.declaration_extraction_run_id
                    == pipeline._active_run_id
                )
            )
        ).all()
    assert len(rows) == 1 and rows[0].attempt_no == 1


@pytest.mark.asyncio
async def test_pass_ledger_hides_cross_tenant_rows_and_denies_mutation(
    app_rw_session, seed_data
) -> None:
    organization_id = seed_data["org_a_id"]
    import_id, run_id, pass_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(organization_id)},
    )
    await app_rw_session.execute(
        text(
            "INSERT INTO declaration_import "
            "(id, organization_id, mode, status, initiated_by, max_cost_usd) "
            "VALUES (:id, :org, 'NEW_PROPERTY', 'PREFLIGHT', :user_id, 1)"
        ),
        {"id": import_id, "org": organization_id, "user_id": seed_data["user_a_admin_id"]},
    )
    await app_rw_session.execute(
        text(
            "INSERT INTO declaration_extraction_run "
            "(id, organization_id, import_id, provider, model_name, prompt_version, schema_version, harness_version) "
            "VALUES (:id, :org, :import_id, 'fake', 'test', 'test', 'test', 'test')"
        ),
        {"id": run_id, "org": organization_id, "import_id": import_id},
    )
    await app_rw_session.execute(
        text(
            "INSERT INTO declaration_import_pass "
            "(id, organization_id, declaration_extraction_run_id, attempt_no, safe_metadata) "
            "VALUES (:id, :org, :run_id, 1, CAST(:metadata AS jsonb))"
        ),
        {
            "id": pass_id,
            "org": organization_id,
            "run_id": run_id,
            "metadata": json.dumps(_entry().as_safe_dict()),
        },
    )
    unsafe_metadata = _entry(attempt_no=2).as_safe_dict() | {"quote": "private document text"}
    malformed_metadata = _entry(attempt_no=2).as_safe_dict() | {"input_tokens": "not-an-integer"}
    over_limit_metadata = _entry(attempt_no=2).as_safe_dict() | {"input_tokens": 1_000_000_001}
    for metadata in (unsafe_metadata, malformed_metadata, over_limit_metadata):
        with pytest.raises(DBAPIError):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(
                    text(
                        "INSERT INTO declaration_import_pass "
                        "(id, organization_id, declaration_extraction_run_id, attempt_no, safe_metadata) "
                        "VALUES (:id, :org, :run_id, 2, CAST(:metadata AS jsonb))"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org": organization_id,
                        "run_id": run_id,
                        "metadata": json.dumps(metadata),
                    },
                )
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(seed_data["org_b_id"])},
    )
    assert await app_rw_session.scalar(
        text("SELECT count(*) FROM declaration_import_pass WHERE id = :id"), {"id": pass_id}
    ) == 0
    for statement in (
        "UPDATE declaration_import_pass SET attempt_no = 2 WHERE id = :id",
        "DELETE FROM declaration_import_pass WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(statement), {"id": pass_id})
