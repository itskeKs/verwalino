import asyncio
import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
from types import SimpleNamespace
import uuid

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.declarations.pipeline as pipeline_module
from tests.declaration_queue_fixture import queue_fixture
from app.core.config import settings
from app.core.db import get_tenant_session
from app.core.storage import compute_sha256
from app.declarations.pipeline import (
    DeclarationPipeline,
    PipelineFailure,
    _Ledger,
    _candidate_drafts,
    _merge_facts,
    _serialize_provider_request,
    _unit_value,
    _unwrap,
    _with_recheck_route_metadata,
    _apply_recheck_route_metadata,
    _values_semantically_equal,
)
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.preflight import DocumentIntegrityError, PdfPreflight
from app.declarations.observability import ProviderPassMetadata
from app.declarations.provider import DocumentInput, PageInput, ProviderCallResult, ProviderFailure, ProviderReservationEstimate, ProviderUsage
from app.declarations.schemas import (
    AdditionalFactPayload,
    CoreExtractionOutput,
    DecimalValue,
    DocumentInventoryOutput,
    FactPayload,
    IntegerValue,
    MeaFraction,
    ReconciliationOutput,
    RuleExtractionOutput,
    RulePayload,
    TargetedRecheckOutput,
    TextValue,
)
from app.models.declaration_candidate import (
    DeclarationCandidate,
    DeclarationCandidateEvidence,
    DeclarationCandidateIssue,
)
from app.models.declaration_import import (
    DeclarationExtractionRun,
    DeclarationImport,
    DeclarationImportDocument,
)
from app.models.property import Property
from app.models.property_document import PropertyDocument
from app.models.unit import Unit
from app.repositories.declaration_candidate_repo import (
    DeclarationCandidateRepository,
    DraftCandidate,
    DraftEvidence,
    DraftIssue,
)
from app.repositories.declaration_import_repo import DeclarationImportRepository
from app.repositories.declaration_import_repo import WorkerLease
from app.services.declaration_import_service import DeclarationImportService
from app.declarations.validation import ValidationIssue
from tests.declaration_fixtures import make_text_pdf


_vision_png = io.BytesIO()
Image.new("RGB", (1, 1), "white").save(_vision_png, format="PNG")
_VISION_DATA_URL = "data:image/png;base64," + base64.b64encode(_vision_png.getvalue()).decode("ascii")


def test_pipeline_module_is_available() -> None:
    assert DeclarationPipeline is not None


def test_malformed_success_usage_charges_reservation_and_fails_closed() -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    reservation = ledger.reserve("input")
    with pytest.raises(PipelineFailure, match="PROVIDER_RESULT_INVALID"):
        _unwrap(
            ProviderCallResult(
                output=DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]),
                usage=None,  # type: ignore[arg-type]
            ),
            DocumentInventoryOutput,
            ledger,
            reservation,
        )
    assert ledger.input_tokens == reservation.input_tokens
    assert ledger.output_tokens == reservation.output_tokens


def test_usage_over_reservation_records_actual_before_stopping() -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    reservation = ledger.reserve("input")
    metadata = _safe_attempt_metadata()
    result = ProviderCallResult(
        output=DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]),
        usage=ProviderUsage(reservation.input_tokens + 1, 1, Decimal("0"), 1),
        metadata=metadata,
        attempt_metadata=(metadata,),
    )
    with pytest.raises(PipelineFailure, match="PROVIDER_USAGE_EXCEEDED_RESERVATION"):
        _unwrap(result, DocumentInventoryOutput, ledger, reservation)
    assert ledger.input_tokens == reservation.input_tokens + 1
    assert len(ledger.pass_entries) == 1


def _safe_attempt_metadata(
    *,
    failure_class: str | None = None,
    attempt_no: int = 1,
    stage: str = "provider",
    pass_id: str = "inventory",
    usage: ProviderUsage | None = None,
) -> ProviderPassMetadata:
    observed = usage or ProviderUsage(1, 1, Decimal("0"), 1)
    route_reasons = (
        (failure_class,)
        if failure_class
        in {
            "PROVIDER_REFUSAL",
            "PROVIDER_INCOMPLETE",
            "PROVIDER_TIMEOUT",
            "PROVIDER_SCHEMA_FAILURE",
        }
        else ()
    )
    return ProviderPassMetadata.from_values(
        pass_id=pass_id,
        attempt_no=attempt_no,
        stage=stage,
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
        input_tokens=observed.input_tokens,
        cached_input_tokens=observed.cached_input_tokens,
        cache_write_input_tokens=observed.cache_write_input_tokens,
        output_tokens=observed.output_tokens,
        estimated_cost_usd=observed.cost_usd,
        latency_ms=1,
        retry_count=attempt_no - 1,
        route_action="HUMAN_REVIEW" if failure_class else "NONE",
        route_reason_codes=route_reasons,
        failure_class=failure_class,
    )


def test_unwrap_collects_safe_success_attempt_metadata_in_memory() -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    reservation = ledger.reserve("input")
    metadata = _safe_attempt_metadata()
    result = ProviderCallResult(
        output=DocumentInventoryOutput(
            documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]
        ),
        usage=ProviderUsage(1, 1, Decimal("0"), 1),
        metadata=metadata,
        attempt_metadata=(metadata,),
    )

    _unwrap(result, DocumentInventoryOutput, ledger, reservation)

    assert len(ledger.pass_entries) == 1
    assert ledger.pass_entries[0].as_safe_dict()["pass_id"] == "inventory"


@pytest.mark.parametrize("usage_complete", ["false", 0, 1, None])
def test_unwrap_rejects_non_boolean_usage_completeness_before_settlement(
    usage_complete: object,
) -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    reservation = ledger.reserve("input")
    metadata = _safe_attempt_metadata()
    result = ProviderCallResult(
        output=DocumentInventoryOutput(
            documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]
        ),
        usage=ProviderUsage(1, 1, Decimal("0"), 1),
        metadata=metadata,
        attempt_metadata=(metadata,),
        usage_complete=usage_complete,  # type: ignore[arg-type]
    )

    with pytest.raises(PipelineFailure, match="PROVIDER_RESULT_INVALID"):
        _unwrap(result, DocumentInventoryOutput, ledger, reservation)

    assert ledger.input_tokens == reservation.input_tokens
    assert ledger.output_tokens == reservation.output_tokens
    assert ledger.pass_entries == []


@pytest.mark.parametrize("case", ["missing", "cardinality", "sequence", "final"])
def test_unwrap_rejects_inconsistent_attempt_metadata_atomically(case: str) -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    reservation = ledger.reserve("input")
    first = _safe_attempt_metadata()
    second = _safe_attempt_metadata(attempt_no=2, stage="schema_repair")
    metadata = second
    attempts = (first, second)
    attempt_count = 2
    if case == "missing":
        attempts = ()
        metadata = None
        attempt_count = 1
    elif case == "cardinality":
        attempts = (first,)
    elif case == "sequence":
        attempts = (first, first)
    elif case == "final":
        metadata = first
    result = ProviderCallResult(
        output=DocumentInventoryOutput(
            documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]
        ),
        usage=ProviderUsage(2, 1, Decimal("0"), attempt_count),
        metadata=metadata,
        attempt_metadata=attempts,
    )

    with pytest.raises(PipelineFailure, match="PROVIDER_RESULT_INVALID"):
        _unwrap(result, DocumentInventoryOutput, ledger, reservation)

    assert ledger.pass_entries == []


def test_unwrap_retains_conservative_reservation_when_usage_observation_is_incomplete() -> None:
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    reservation = ledger.reserve("input", reserved_input_tokens=20)
    ledger.charge_reservation(reservation)
    metadata = _safe_attempt_metadata()
    repair_metadata = _safe_attempt_metadata(attempt_no=2, stage="schema_repair")
    result = ProviderCallResult(
        output=DocumentInventoryOutput(
            documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]
        ),
        usage=ProviderUsage(7, 1, Decimal("0"), 2),
        metadata=repair_metadata,
        attempt_metadata=(metadata, repair_metadata),
        usage_complete=False,
    )

    _unwrap(
        result,
        DocumentInventoryOutput,
        ledger,
        reservation,
        reservation_already_charged=True,
    )

    assert ledger.input_tokens == reservation.input_tokens
    assert ledger.output_tokens == reservation.output_tokens
    assert ledger.cost_usd == reservation.cost_usd
    assert ledger.attempts == 2
    assert ledger.pass_entries


def test_recheck_attempt_metadata_keeps_vision_route_on_success() -> None:
    metadata = _safe_attempt_metadata()
    raw = ProviderCallResult(
        output=DocumentInventoryOutput(
            documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "D"}]
        ),
        usage=ProviderUsage(1, 1, Decimal("0"), 1),
        metadata=metadata,
        attempt_metadata=(metadata,),
    )

    routed = _with_recheck_route_metadata(raw, ("LOW_PAGE_QUALITY", "untrusted"))

    assert isinstance(routed, ProviderCallResult)
    assert routed.metadata is not None
    assert routed.metadata.route_action == "VISION_RECHECK"
    assert routed.metadata.route_reason_codes == ("LOW_PAGE_QUALITY",)


def test_recheck_success_route_rejects_missing_final_metadata_before_decoration() -> None:
    metadata = _safe_attempt_metadata(pass_id="recheck")
    raw = ProviderCallResult(
        output=TargetedRecheckOutput(),
        usage=ProviderUsage(1, 1, Decimal("0"), 1),
        metadata=None,
        attempt_metadata=(metadata,),
    )

    with pytest.raises(PipelineFailure, match="PROVIDER_RESULT_INVALID"):
        _with_recheck_route_metadata(raw, ("LOW_PAGE_QUALITY",))


def test_recheck_failure_metadata_keeps_vision_route_and_failure_class() -> None:
    metadata = _safe_attempt_metadata(failure_class="PROVIDER_TIMEOUT")
    error = ProviderFailure(
        "PROVIDER_TIMEOUT", True, "safe", attempt_metadata=(metadata,)
    )

    _apply_recheck_route_metadata(error, ("EVIDENCE_QUOTE_NOT_FOUND",))

    assert error.metadata is not None
    assert error.metadata.route_action == "VISION_RECHECK"
    assert error.metadata.route_reason_codes == ("EVIDENCE_QUOTE_NOT_FOUND",)
    assert error.metadata.failure_class == "PROVIDER_TIMEOUT"


def test_recheck_failure_route_rejects_inconsistent_final_metadata_before_decoration() -> None:
    first = _safe_attempt_metadata(
        failure_class="PROVIDER_TIMEOUT", pass_id="recheck"
    )
    inconsistent = _safe_attempt_metadata(
        failure_class="PROVIDER_TIMEOUT",
        pass_id="recheck",
        attempt_no=2,
        stage="schema_repair",
    )
    error = ProviderFailure(
        "PROVIDER_TIMEOUT",
        True,
        "safe",
        attempt_metadata=(first,),
        metadata=inconsistent,
        attempt_count=1,
    )

    with pytest.raises(PipelineFailure, match="PROVIDER_RESULT_INVALID"):
        _apply_recheck_route_metadata(error, ("EVIDENCE_QUOTE_NOT_FOUND",))


@pytest.mark.asyncio
async def test_call_collects_safe_failure_attempt_metadata_in_memory() -> None:
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings

    async def checkpoint(_ledger) -> None:
        return None

    async def fail(*_args, **_kwargs):
        metadata = _safe_attempt_metadata(failure_class="PROVIDER_TIMEOUT")
        raise ProviderFailure(
            "PROVIDER_TIMEOUT", True, "safe", attempt_metadata=(metadata,)
        )

    pipeline._checkpoint = checkpoint
    pipeline._provider = SimpleNamespace(
        inventory=fail,
        estimate_reservation=lambda *_args, **_kwargs: ProviderReservationEstimate(1, 1, (1,)),
    )
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)

    with pytest.raises(ProviderFailure):
        await pipeline._call("inventory", (), ledger, DocumentInventoryOutput)

    assert len(ledger.pass_entries) == 1
    assert ledger.pass_entries[0].failure_class == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_call_rejects_invalid_failure_contract_before_usage_branch() -> None:
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings

    async def checkpoint(_ledger) -> None:
        return None

    async def fail(*_args, **_kwargs):
        first = _safe_attempt_metadata(failure_class="PROVIDER_TIMEOUT")
        second = _safe_attempt_metadata(
            failure_class="PROVIDER_TIMEOUT", attempt_no=2, stage="schema_repair"
        )
        error = ProviderFailure(
            "PROVIDER_TIMEOUT",
            True,
            "safe",
            attempt_metadata=(first, second),
            attempt_count=2,
        )
        error.usage_complete = "false"  # type: ignore[assignment]
        raise error

    pipeline._checkpoint = checkpoint
    pipeline._provider = SimpleNamespace(
        inventory=fail,
        estimate_reservation=lambda *_args, **_kwargs: ProviderReservationEstimate(1, 1, (1,)),
    )
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)

    with pytest.raises(PipelineFailure, match="PROVIDER_RESULT_INVALID"):
        await pipeline._call("inventory", (), ledger, DocumentInventoryOutput)

    assert ledger.pass_entries == []


def test_output_allowances_descend_exactly_across_logical_calls() -> None:
    configured = settings.model_copy(
        update={"declaration_provider_max_output_tokens_per_call": 10}
    )
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 25, Decimal("1"), configured)
    reservations = []
    for _ in range(3):
        reservation = ledger.reserve("input")
        reservations.append(reservation.output_tokens)
        ledger.charge_reservation(reservation)
    assert reservations == [10, 10, 5]
    assert ledger.output_allowance() == 0


@pytest.mark.asyncio
async def test_pre_call_checkpoint_loss_prevents_provider_request() -> None:
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings
    called = False

    async def lose_lease(_ledger) -> None:
        raise PipelineFailure("LEASE_LOST")

    async def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    pipeline._checkpoint = lose_lease
    pipeline._provider = SimpleNamespace(
        inventory=should_not_run,
        estimate_reservation=lambda *_args, **_kwargs: ProviderReservationEstimate(10, 1, (10,)),
    )
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._call("inventory", (), ledger, DocumentInventoryOutput)
    assert called is False
    assert ledger.input_tokens > 0


@pytest.mark.asyncio
async def test_pipeline_reserves_luna_cache_write_worst_case_before_provider_request() -> None:
    configured = settings.model_copy(
        update={"declaration_provider_max_output_tokens_per_call": 10}
    )
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    called = False

    async def checkpoint(_ledger) -> None:
        return None

    async def should_not_run(*_args, **_kwargs) -> None:
        nonlocal called
        called = True

    pipeline._checkpoint = checkpoint
    pipeline._provider = SimpleNamespace(
        inventory=should_not_run,
        estimate_reservation=lambda *_args, **_kwargs: ProviderReservationEstimate(100, 1, (100,)),
    )
    # 100 input tokens at the ordinary 0.20 rate plus ten output tokens at
    # 1.20 would fit exactly.  A Luna cache write costs 1.25x the ordinary
    # input rate and must be reserved before the provider is reachable.
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("0.000032"), configured)

    with pytest.raises(PipelineFailure, match="IMPORT_COST_BUDGET_EXCEEDED"):
        await pipeline._call("inventory", (), ledger, DocumentInventoryOutput)

    assert called is False
    assert ledger.cost_usd == Decimal("0")


@pytest.mark.asyncio
async def test_full_serialized_request_cap_fails_before_provider_call() -> None:
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings.model_copy(update={"declaration_provider_batch_chars": 10})
    called = False

    async def checkpoint(_ledger) -> None:
        return None

    async def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not receive an over-cap request")

    pipeline._checkpoint = checkpoint
    pipeline._provider = SimpleNamespace(inventory=should_not_run)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), pipeline._settings)
    documents = (DocumentInput("d", (PageInput("d", 1, "text", 1.0),)),)
    with pytest.raises(PipelineFailure, match="PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED"):
        await pipeline._call("inventory", documents, ledger, DocumentInventoryOutput)
    assert called is False


def test_serialized_recheck_envelope_redacts_image_data_url() -> None:
    image = "data:image/png;base64,super-secret-image-bytes"
    serialized = _serialize_provider_request(
        "recheck",
        documents=(DocumentInput("d", (PageInput("d", 1, "text", 1.0, image),)),),
        issue_codes=("LOW_PAGE_QUALITY",),
        subject_keys=(),
    )

    assert image not in serialized
    assert "<image-data-url-omitted>" in serialized


def test_fixed_pass_page_batching_maps_unrepresentable_page_to_stable_cap_code() -> None:
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings.model_copy(update={"declaration_provider_batch_chars": 10})

    with pytest.raises(PipelineFailure, match="PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED"):
        pipeline._page_batches((PageInput("d", 1, "text", 1.0),))


@pytest.mark.asyncio
async def test_missing_provider_estimator_fails_closed_before_provider_call() -> None:
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings
    called = False

    async def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True

    pipeline._provider = SimpleNamespace(inventory=should_not_run)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    documents = (DocumentInput("d", (PageInput("d", 1, "text", 1.0),)),)

    with pytest.raises(PipelineFailure, match="PROVIDER_RESERVATION_ESTIMATE_INVALID"):
        await pipeline._call("inventory", documents, ledger, DocumentInventoryOutput)
    assert called is False


class _ResponsesMock:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    async def parse(self, **_kwargs):
        return self._outcomes.pop(0)


@pytest.mark.asyncio
async def test_real_adapter_estimate_settles_tiny_inventory_without_overage() -> None:
    output = DocumentInventoryOutput(
        documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]
    )
    documents = (DocumentInput("d", (PageInput("d", 1, "x", 1.0),)),)
    client = SimpleNamespace(responses=_ResponsesMock([]))
    provider = OpenAiDeclarationProvider(
        client=client,
        primary_model="gpt-5.6-luna",
        escalation_model="gpt-5.4-mini",
    )
    estimate = provider.estimate_reservation(
        "inventory", documents=documents, max_output_tokens=10
    )
    client.responses._outcomes.append(
        SimpleNamespace(
            output_parsed=output,
            usage=SimpleNamespace(
                input_tokens=estimate.attempt_input_tokens[0],
                output_tokens=1,
                input_tokens_details=SimpleNamespace(
                    cached_tokens=0, cache_write_tokens=0
                ),
            ),
            status="completed",
            refusal=None,
        )
    )
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings.model_copy(
        update={"declaration_provider_max_output_tokens_per_call": 10}
    )
    pipeline._provider = provider

    async def checkpoint(_ledger):
        return None

    pipeline._checkpoint = checkpoint
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), pipeline._settings)
    result = await pipeline._call("inventory", documents, ledger, DocumentInventoryOutput)

    assert result == output
    assert ledger.input_tokens == estimate.attempt_input_tokens[0]
    assert ledger.output_tokens == 1


@pytest.mark.asyncio
async def test_real_adapter_repair_usage_is_settled_once_within_aggregate_reservation() -> None:
    output = DocumentInventoryOutput(
        documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]
    )
    documents = (DocumentInput("d", (PageInput("d", 1, "x", 1.0),)),)
    client = SimpleNamespace(responses=_ResponsesMock([]))
    provider = OpenAiDeclarationProvider(
        client=client,
        primary_model="gpt-5.6-luna",
        escalation_model="gpt-5.4-mini",
    )
    estimate = provider.estimate_reservation(
        "inventory", documents=documents, max_output_tokens=10
    )
    client.responses._outcomes.extend(
        [
            SimpleNamespace(
                output_parsed=None,
                usage=SimpleNamespace(
                    input_tokens=estimate.attempt_input_tokens[0],
                    output_tokens=1,
                    input_tokens_details=SimpleNamespace(
                        cached_tokens=0, cache_write_tokens=0
                    ),
                ),
                status="completed",
                refusal=None,
            ),
            SimpleNamespace(
                output_parsed=output,
                usage=SimpleNamespace(
                    input_tokens=estimate.attempt_input_tokens[1],
                    output_tokens=1,
                    input_tokens_details=SimpleNamespace(
                        cached_tokens=0, cache_write_tokens=0
                    ),
                ),
                status="completed",
                refusal=None,
            ),
        ]
    )
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings.model_copy(
        update={"declaration_provider_max_output_tokens_per_call": 10}
    )
    pipeline._provider = provider

    async def checkpoint(_ledger):
        return None

    pipeline._checkpoint = checkpoint
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), pipeline._settings)
    await pipeline._call("inventory", documents, ledger, DocumentInventoryOutput)

    assert ledger.input_tokens == estimate.input_tokens
    assert ledger.output_tokens == 2
    assert ledger.attempts == 2


def test_equal_fact_merge_unions_evidence_min_confidence_and_issue_codes() -> None:
    base = {
        "subject_key": "property:new",
        "field": "street",
        "raw_value": "Talstraße 1",
        "normalized_value": {"kind": "TEXT", "value": "Talstraße 1"},
    }
    first = FactPayload.model_validate(
        {
            **base,
            "confidence": 0.95,
            "issues": ["first"],
            "evidence": [{"document_key": "d", "pdf_page": 1, "quote": "A"}],
        }
    )
    second = FactPayload.model_validate(
        {
            **base,
            "confidence": 0.86,
            "issues": ["second"],
            "evidence": [{"document_key": "d", "pdf_page": 2, "quote": "B"}],
        }
    )
    merged = _merge_facts([[first], [second]])
    assert len(merged) == 1
    assert merged[0].confidence == 0.86
    assert merged[0].issues == ["first", "second"]
    assert [item.pdf_page for item in merged[0].evidence] == [1, 2]


def test_each_conflicting_fixed_fact_gets_its_own_candidate_bound_blocker() -> None:
    evidence = [{"document_key": "d", "pdf_page": 1, "quote": "Beleg"}]
    facts = [
        FactPayload(subject_key="property:new", field="street", raw_value=value, normalized_value=TextValue(kind="TEXT", value=value), confidence=0.9, evidence=evidence)
        for value in ("Talstraße 1", "Talstraße 2")
    ] + [
        FactPayload(subject_key="property:new", field="city", raw_value=value, normalized_value=TextValue(kind="TEXT", value=value), confidence=0.9, evidence=evidence)
        for value in ("Düsseldorf", "Köln")
    ]
    imported = SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None)
    drafts, issues = _candidate_drafts(
        imported,
        DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]),
        ReconciliationOutput(facts=facts, rules=[]),
        (),
        {"d": uuid.uuid4()},
        None,
        (),
    )

    assert {item.candidate_stable_key for item in issues if item.code == "CONFLICTING_ALTERNATIVES"} == {
        "fact:property:new:street",
        "fact:property:new:city",
    }
    assert {draft.stable_key for draft in drafts} >= {
        "fact:property:new:street",
        "fact:property:new:city",
    }


def test_additional_management_fact_becomes_a_valid_narrow_rule_candidate() -> None:
    document_id = uuid.uuid4()
    additional_fact = AdditionalFactPayload(
        subject_key="additional:heating-cost-allocation",
        label="Heizkostenverteilung",
        value="Die Heizkosten werden nach Verbrauch und Wohnfläche verteilt.",
        management_relevance="Bei Jahresabrechnungen und Wirtschaftsplänen berücksichtigen.",
        evidence=[{"document_key": "d", "pdf_page": 3, "quote": "Heizkosten nach Verbrauch und Fläche."}],
        confidence=0.91,
        clarification_needed=False,
    )

    drafts, _issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]),
        ReconciliationOutput(facts=[], rules=[], additional_facts=[additional_fact]),
        (),
        {"d": document_id},
        None,
        (),
    )

    candidate = next(item for item in drafts if item.stable_key == "additional-rule:additional:heating-cost-allocation")
    assert candidate.proposed_value_json == {
        "topic": "sonstige_relevante_information",
        "title": "Heizkostenverteilung",
        "summary": "Die Heizkosten werden nach Verbrauch und Wohnfläche verteilt.",
        "scope": {"additional_scope": ["additional:heating-cost-allocation"]},
        "effect": {"administrative_note": "Bei Jahresabrechnungen und Wirtschaftsplänen berücksichtigen."},
        "clarification_needed": False,
    }
    assert candidate.evidence[0].field_path is None


def test_rule_and_additional_fact_with_same_subject_remain_separate_candidates_with_own_evidence() -> None:
    document_id = uuid.uuid4()
    rule_evidence = [{"document_key": "d", "pdf_page": 3, "quote": "Heizkosten nach Verbrauch."}]
    additional_evidence = [{"document_key": "d", "pdf_page": 3, "quote": "Heizkosten nach Fläche."}]
    rule = RulePayload(
        subject_key="rule:heating-cost-allocation",
        topic="heating_costs",
        title="Heizkostenverteilung",
        summary="Die Heizkosten werden nach Verbrauch und Wohnfläche verteilt.",
        scope={"all_units": True},
        effect={"administrative_note": "Regelmäßig in der Abrechnung beachten."},
        evidence=rule_evidence,
        confidence=0.91,
        clarification_needed=False,
    )
    additional_fact = AdditionalFactPayload(
        subject_key="rule:heating-cost-allocation",
        label="Heizkostenverteilung",
        value="Die Heizkosten werden nach Verbrauch und Wohnfläche verteilt.",
        management_relevance="Bei Jahresabrechnungen und Wirtschaftsplänen berücksichtigen.",
        evidence=additional_evidence,
        confidence=0.91,
        clarification_needed=False,
    )

    drafts, issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]),
        ReconciliationOutput(facts=[], rules=[rule], additional_facts=[additional_fact]),
        (),
        {"d": document_id},
        None,
        (),
    )

    matching = [draft for draft in drafts if draft.candidate_type == "RULE" and draft.subject_key == rule.subject_key]
    assert len(matching) == 2
    by_key = {draft.stable_key: draft for draft in matching}
    assert set(by_key) == {
        "rule:rule:heating-cost-allocation",
        "additional-rule:rule:heating-cost-allocation",
    }
    assert by_key["rule:rule:heating-cost-allocation"].evidence[0].quote == "Heizkosten nach Verbrauch."
    assert by_key["additional-rule:rule:heating-cost-allocation"].evidence[0].quote == "Heizkosten nach Fläche."
    assert all(
        issue.code == "DUPLICATE_RULE_SUBJECT" and issue.severity == "BLOCKER"
        for issue in issues
    )
    assert {issue.candidate_stable_key for issue in issues} == set(by_key)


def test_distinct_regular_rules_with_the_same_subject_have_deterministic_own_candidates_and_sources() -> None:
    document_id = uuid.uuid4()
    rules = [
        RulePayload(
            subject_key="rule:heating-cost-allocation",
            topic="heating_costs",
            title=title,
            summary=summary,
            scope={"all_units": True},
            effect={"administrative_note": note},
            evidence=[{"document_key": "d", "pdf_page": 3, "quote": quote}],
            confidence=0.91,
            clarification_needed=False,
        )
        for title, summary, note, quote in (
            ("Heizkosten nach Verbrauch", "Die Heizkosten werden nach Verbrauch verteilt.", "Verbrauchswerte abrechnen.", "Heizkosten nach Verbrauch."),
            ("Heizkosten nach Fläche", "Die Heizkosten werden nach Fläche verteilt.", "Flächenwerte abrechnen.", "Heizkosten nach Fläche."),
        )
    ]

    def drafts():
        return _candidate_drafts(
            SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
            DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]),
            ReconciliationOutput(facts=[], rules=rules, additional_facts=[]),
            (),
            {"d": document_id},
            None,
            (),
        )

    first, issues = drafts()
    second, _ = drafts()
    matching = [draft for draft in first if draft.candidate_type == "RULE" and draft.subject_key == rules[0].subject_key]
    repeated = [draft for draft in second if draft.candidate_type == "RULE" and draft.subject_key == rules[0].subject_key]
    assert len(matching) == 2
    assert [draft.stable_key for draft in matching] == [draft.stable_key for draft in repeated]
    assert len({draft.stable_key for draft in matching}) == 2
    assert all(draft.stable_key.startswith("rule:rule:heating-cost-allocation:") for draft in matching)
    assert {draft.evidence[0].quote for draft in matching} == {
        "Heizkosten nach Verbrauch.",
        "Heizkosten nach Fläche.",
    }
    assert all(len(draft.evidence) == 1 and draft.evidence[0].field_path is None for draft in matching)
    assert {issue.candidate_stable_key for issue in issues if issue.code == "DUPLICATE_RULE_SUBJECT"} == {
        draft.stable_key for draft in matching
    }


def test_identical_regular_rule_variants_choose_one_deterministic_source_per_persisted_slot() -> None:
    document_id = uuid.uuid4()
    common = {
        "subject_key": "rule:heating-cost-allocation",
        "topic": "heating_costs",
        "title": "Heizkosten nach Verbrauch",
        "summary": "Die Heizkosten werden nach Verbrauch verteilt.",
        "scope": {"all_units": True},
        "effect": {"administrative_note": "Verbrauchswerte abrechnen."},
        "confidence": 0.91,
        "clarification_needed": False,
    }
    output = ReconciliationOutput(
        facts=[],
        rules=[
            RulePayload(evidence=[{"document_key": "d", "pdf_page": 3, "quote": quote}], **common)
            for quote in ("Zweite Belegstelle.", "Erste Belegstelle.")
        ],
        additional_facts=[],
    )

    drafts, issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]),
        output,
        (),
        {"d": document_id},
        None,
        (),
    )

    matching = [draft for draft in drafts if draft.candidate_type == "RULE"]
    assert len(matching) == 1
    assert matching[0].stable_key == "rule:rule:heating-cost-allocation"
    assert [source.quote for source in matching[0].evidence] == ["Erste Belegstelle."]
    assert not [issue for issue in issues if issue.code == "DUPLICATE_RULE_SUBJECT"]


def test_nested_conflict_alternatives_bind_blockers_to_unit_legal_and_assignment_candidates() -> None:
    evidence = [{"document_key": "d", "pdf_page": 1, "quote": "Beleg"}]
    facts = [
        FactPayload(subject_key=subject, field=field, raw_value=value, normalized_value=TextValue(kind="TEXT", value=value), confidence=0.9, evidence=evidence)
        for subject, field, values in (
            ("unit:new", "number", ("1", "2")),
            ("legal_reference:grundbuch", "reference_type", ("A", "B")),
            ("assignment:owner", "designation", ("Max", "Erika")),
        )
        for value in values
    ]
    _drafts, issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]),
        ReconciliationOutput(facts=facts, rules=[]),
        (),
        {"d": uuid.uuid4()},
        None,
        (),
    )

    assert {item.candidate_stable_key for item in issues if item.code == "CONFLICTING_ALTERNATIVES"} >= {
        "unit-create:unit:new",
        "legal-reference:legal_reference:grundbuch",
        "assignment:assignment:owner",
    }


@pytest.mark.asyncio
async def test_conflicting_fixed_fact_blockers_persist_on_each_candidate(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    evidence = [{"document_key": document_key, "pdf_page": 1, "quote": "Beleg"}]
    facts = [
        FactPayload(subject_key="property:new", field=field, raw_value=value, normalized_value=TextValue(kind="TEXT", value=value), confidence=0.9, evidence=evidence)
        for field, values in {"street": ("Talstraße 1", "Talstraße 2"), "city": ("Düsseldorf", "Köln")}.items()
        for value in values
    ]
    drafts, draft_issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{"document_key": document_key, "document_type": "DECLARATION", "title": "Teilung"}]),
        ReconciliationOutput(facts=facts, rules=[]),
        (),
        {document_key: uuid.UUID(document_key)},
        None,
        (),
    )
    async with get_tenant_session(lease.organization_id) as session:
        await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
            lease.import_id, drafts, draft_issues
        )
    async with get_tenant_session(lease.organization_id) as session:
        rows = await session.execute(
            select(DeclarationCandidate.stable_key)
            .join(DeclarationCandidateIssue, DeclarationCandidateIssue.candidate_id == DeclarationCandidate.id)
            .where(DeclarationCandidate.import_id == lease.import_id)
            .where(DeclarationCandidateIssue.code == "CONFLICTING_ALTERNATIVES")
        )
    assert set(rows.scalars()) == {"fact:property:new:street", "fact:property:new:city"}


@pytest.mark.asyncio
async def test_global_mea_issue_persists_original_reconciliation_set_status(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    output = ReconciliationOutput(facts=[], rules=[], mea_set_status="PARTIAL")
    _drafts, draft_issues = _candidate_drafts(
        SimpleNamespace(mode="NEW_PROPERTY", target_property_id=None),
        DocumentInventoryOutput(documents=[{"document_key": document_key, "document_type": "DECLARATION", "title": "Teilung"}]),
        output,
        (ValidationIssue("MEA_TOTAL_INVALID", "BLOCKER", "invalid total"),),
        {document_key: uuid.UUID(document_key)},
        None,
        (),
    )
    issue = next(item for item in draft_issues if item.code == "MEA_TOTAL_INVALID")
    assert issue.details_json == {"mea_set_status": "PARTIAL"}

    async with get_tenant_session(lease.organization_id) as session:
        await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
            lease.import_id, (), draft_issues
        )
    async with get_tenant_session(lease.organization_id) as session:
        persisted = await session.scalar(
            select(DeclarationCandidateIssue).where(
                DeclarationCandidateIssue.import_id == lease.import_id,
                DeclarationCandidateIssue.code == "MEA_TOTAL_INVALID",
            )
        )
    assert persisted is not None
    assert persisted.details_json == {"mea_set_status": "PARTIAL"}


def test_decimal_comparison_is_semantic_and_previous_area_is_schema_compatible() -> None:
    unit = SimpleNamespace(area=Decimal("50.00"))
    previous = _unit_value(unit, "area", None)
    assert previous == {"kind": "DECIMAL", "value": "50.00", "unit": "m²"}
    assert _values_semantically_equal(
        {"kind": "DECIMAL", "value": "50", "unit": "m²"}, previous
    )
    assert not _values_semantically_equal(
        {"kind": "DECIMAL", "value": "50", "unit": "m²"},
        {"kind": "DECIMAL", "value": "50", "unit": "m² beheizt"},
    )


class _MemoryStore:
    def __init__(self, data: dict[str, bytes]) -> None:
        self._data = data

    def get(self, key: str) -> bytes:
        return self._data[key]


class _RecordingProvider:
    def __init__(
        self,
        document_key: str,
        *,
        usage: ProviderUsage | None = None,
        evidence_quote: str = "Teilungserklärung",
    ) -> None:
        self.calls: list[str] = []
        self._usage = usage or ProviderUsage(13, 7, Decimal("0.000001"), 1)
        evidence = [{"document_key": document_key, "pdf_page": 1, "quote": evidence_quote}]
        street = FactPayload(
            subject_key="property:main",
            field="street",
            raw_value="Hauptstraße 1",
            normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
            confidence=0.99,
            evidence=evidence,
        )
        denominator = FactPayload(
            subject_key="property:main",
            field="mea_denominator",
            raw_value="1",
            normalized_value=IntegerValue(kind="INTEGER", value=1),
            confidence=0.99,
            evidence=evidence,
        )
        number = FactPayload(
            subject_key="unit:one",
            field="number",
            raw_value="1",
            normalized_value=TextValue(kind="TEXT", value="1"),
            confidence=0.99,
            evidence=evidence,
        )
        mea = FactPayload(
            subject_key="unit:one",
            field="mea",
            raw_value="1/1",
            normalized_value=MeaFraction(kind="MEA", numerator=1, denominator=1),
            confidence=0.99,
            evidence=evidence,
        )
        self.inventory_output = DocumentInventoryOutput(
            documents=[{"document_key": document_key, "document_type": "DECLARATION", "title": "Teilung"}]
        )
        self.core_output = CoreExtractionOutput(facts=[street, denominator, number, mea])
        self.rules_output = RuleExtractionOutput(rules=[])
        self.reconciliation_output = ReconciliationOutput(
            facts=[street, denominator, number, mea], rules=[], mea_set_status="COMPLETE"
        )

    def _result(self, pass_id: str, output):
        metadata = _safe_attempt_metadata(
            pass_id=pass_id,
            usage=self._usage,
        )
        return ProviderCallResult(
            output.model_copy(deep=True),
            self._usage,
            metadata=metadata,
            attempt_metadata=(metadata,),
        )

    def estimate_reservation(self, method, *, max_output_tokens, **request):
        return ProviderReservationEstimate(
            self._usage.input_tokens,
            1,
            (self._usage.input_tokens,),
        )

    async def inventory(self, documents, *, max_output_tokens):
        self.calls.append("inventory")
        return self._result("inventory", self.inventory_output)

    async def extract_core(self, documents, *, max_output_tokens):
        self.calls.append("extract_core")
        return self._result("extract_core", self.core_output)

    async def extract_rules(self, documents, *, max_output_tokens):
        self.calls.append("extract_rules")
        return self._result("extract_rules", self.rules_output)

    async def reconcile(self, inventory, core, rules, *, max_output_tokens):
        self.calls.append("reconcile")
        return self._result("reconcile", self.reconciliation_output)

    async def recheck(self, documents, issue_codes, subject_keys, *, max_output_tokens):
        self.calls.append("recheck")
        return self._result("recheck", TargetedRecheckOutput())


class _ReservationProbeProvider(_RecordingProvider):
    def __init__(self, document_key: str, organization_id: uuid.UUID, import_id: uuid.UUID) -> None:
        super().__init__(document_key)
        self._organization_id = organization_id
        self._import_id = import_id
        self.before_inventory_tokens: int | None = None

    async def inventory(self, documents, *, max_output_tokens):
        async with get_tenant_session(self._organization_id) as session:
            item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == self._import_id))
            self.before_inventory_tokens = None if item is None else int(item.input_tokens)
        return await super().inventory(documents, max_output_tokens=max_output_tokens)


class _NoUsageFailureProvider(_RecordingProvider):
    async def inventory(self, documents, *, max_output_tokens):
        self.calls.append("inventory")
        raise ProviderFailure("PROVIDER_TIMEOUT", True, "safe", usage=None)


class _BareResultProvider(_RecordingProvider):
    async def inventory(self, documents, *, max_output_tokens):
        self.calls.append("inventory")
        return self.inventory_output


class _WrongTypedResultProvider(_RecordingProvider):
    """A result wrapper is insufficient when its typed payload is wrong."""

    async def inventory(self, documents, *, max_output_tokens):
        self.calls.append("inventory")
        return ProviderCallResult(
            CoreExtractionOutput(facts=[]),
            self._usage,
        )


class _OverReservationProvider(_RecordingProvider):
    async def inventory(self, documents, *, max_output_tokens):
        self.calls.append("inventory")
        usage = ProviderUsage(
            self._usage.input_tokens + 1, 1, Decimal("0.000001"), 1
        )
        metadata = _safe_attempt_metadata(usage=usage)
        return ProviderCallResult(
            self.inventory_output.model_copy(deep=True),
            usage,
            metadata=metadata,
            attempt_metadata=(metadata,),
        )


class _AttachedUsageFailureProvider(_RecordingProvider):
    async def inventory(self, documents, *, max_output_tokens):
        self.calls.append("inventory")
        usage = ProviderUsage(5, 2, Decimal("0.000001"), 1)
        metadata = _safe_attempt_metadata(
            failure_class="PROVIDER_PERMANENT", usage=usage
        )
        raise ProviderFailure(
            "PROVIDER_PERMANENT",
            False,
            "safe",
            usage,
            attempt_metadata=(metadata,),
        )


class _OutOfRangeRelationProvider(_RecordingProvider):
    def __init__(self, document_key: str) -> None:
        super().__init__(document_key)
        self.inventory_output = DocumentInventoryOutput(
            documents=[
                {
                    "document_key": document_key,
                    "document_type": "DECLARATION",
                    "title": "Teilung",
                    "relations": [
                        {
                            "relation_type": "SUPPLEMENTS",
                            "target_document_key": document_key,
                            "evidence": [{"document_key": document_key, "pdf_page": 99, "quote": "Beleg"}],
                        }
                    ],
                }
            ]
        )


class _UnknownEvidenceProvider(_RecordingProvider):
    def __init__(self, document_key: str) -> None:
        super().__init__(document_key)
        self.reconciliation_output.facts[0].evidence[0].document_key = "unknown-document"


class _BudgetSkippedRecheckProvider(_RecordingProvider):
    def __init__(self, document_key: str) -> None:
        super().__init__(document_key)
        self.recheck_estimates = 0
        self.reconciliation_output.facts[0].evidence[0].quote = "nicht vorhandene Formulierung"

    def estimate_reservation(self, method, *, max_output_tokens, **request):
        if method == "recheck":
            self.recheck_estimates += 1
        return super().estimate_reservation(method, max_output_tokens=max_output_tokens, **request)

    async def recheck(self, *args, **kwargs):
        raise AssertionError("a depleted output budget must prevent recheck provider I/O")


class _CapturingRecheckProvider(_RecordingProvider):
    def __init__(self, document_key: str) -> None:
        super().__init__(document_key)
        self.recheck_pages: list[int] = []
        self.recheck_images: list[str | None] = []

    async def recheck(self, documents, issue_codes, subject_keys, *, max_output_tokens):
        self.recheck_pages = [page.pdf_page for document in documents for page in document.pages]
        self.recheck_images = [
            page.image_data_url for document in documents for page in document.pages
        ]
        return await super().recheck(
            documents, issue_codes, subject_keys, max_output_tokens=max_output_tokens
        )


class _ReconcileRecordingProvider:
    """Returns each bounded reconcile part so merge behavior is observable."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[str], list[str]]] = []

    def estimate_reservation(self, method, *, max_output_tokens, **request):
        assert method == "reconcile"
        return ProviderReservationEstimate(1, 1, (1,))

    async def reconcile(self, inventory, core, rules, *, max_output_tokens):
        self.calls.append((
            [fact.raw_value for fact in core.facts],
            [item.value for item in core.additional_facts],
            [rule.title for rule in rules.rules],
        ))
        usage = ProviderUsage(1, 1, Decimal("0"), 1)
        metadata = _safe_attempt_metadata(pass_id="reconcile", usage=usage)
        return ProviderCallResult(
            ReconciliationOutput(
                facts=[item.model_copy(deep=True) for item in core.facts],
                rules=[item.model_copy(deep=True) for item in rules.rules],
                additional_facts=[
                    item.model_copy(deep=True)
                    for item in [*core.additional_facts, *rules.additional_facts]
                ],
            ),
            usage,
            metadata=metadata,
            attempt_metadata=(metadata,),
        )


class _DetailedBatchProvider(_RecordingProvider):
    def __init__(self, document_key: str, **kwargs) -> None:
        super().__init__(document_key, **kwargs)
        self.call_pages: list[tuple[str, tuple[int, ...]]] = []
        self.serialized_request_sizes: list[tuple[str, int]] = []

    def _record(self, method: str, documents) -> None:
        self.call_pages.append(
            (method, tuple(page.pdf_page for document in documents for page in document.pages))
        )
        self.serialized_request_sizes.append(
            (method, len(_serialize_provider_request(method, documents=documents)))
        )

    async def inventory(self, documents, *, max_output_tokens):
        self._record("inventory", documents)
        return await super().inventory(documents, max_output_tokens=max_output_tokens)

    async def extract_core(self, documents, *, max_output_tokens):
        self._record("extract_core", documents)
        return await super().extract_core(documents, max_output_tokens=max_output_tokens)

    async def extract_rules(self, documents, *, max_output_tokens):
        self._record("extract_rules", documents)
        return await super().extract_rules(documents, max_output_tokens=max_output_tokens)

    async def reconcile(self, inventory, core, rules, *, max_output_tokens):
        self.call_pages.append(("reconcile", ()))
        return await super().reconcile(inventory, core, rules, max_output_tokens=max_output_tokens)


def _reconcile_fact(value: str, *, issues: list[str] | None = None) -> FactPayload:
    return FactPayload(
        subject_key="property:main",
        field="street",
        raw_value=value,
        normalized_value=TextValue(kind="TEXT", value=value),
        confidence=0.99,
        evidence=[{"document_key": "d", "pdf_page": 1, "quote": "Beleg"}],
        issues=issues or [],
    )


def _bounded_reconcile_pipeline(provider, configured):
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._provider = provider
    pipeline._settings = configured

    async def checkpoint(_ledger):
        return None

    pipeline._checkpoint = checkpoint
    return pipeline


def _reconcile_ledger(configured):
    return _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), configured)


@pytest.mark.asyncio
async def test_reconcile_exact_serialized_cap_accepts_and_one_character_over_splits_without_loss() -> None:
    inventory = DocumentInventoryOutput(
        documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]
    )
    first = _reconcile_fact("first")
    exact_second = _reconcile_fact("second", issues=["x"])
    exact_core = CoreExtractionOutput(facts=[first, exact_second])
    rules = RuleExtractionOutput(rules=[])
    exact_cap = len(_serialize_provider_request("reconcile", inventory=inventory, core=exact_core, rules=rules))
    configured = settings.model_copy(update={"declaration_provider_batch_chars": exact_cap})
    exact_provider = _ReconcileRecordingProvider()
    exact = await _bounded_reconcile_pipeline(exact_provider, configured)._reconcile_bounded(
        inventory, exact_core, rules, _reconcile_ledger(configured)
    )
    assert exact_provider.calls == [(["first", "second"], [], [])]
    assert [fact.raw_value for fact in exact.facts] == ["first", "second"]

    over_second = _reconcile_fact("second", issues=["xx"])
    over_core = CoreExtractionOutput(facts=[first, over_second])
    assert len(_serialize_provider_request("reconcile", inventory=inventory, core=over_core, rules=rules)) == exact_cap + 1
    split_provider = _ReconcileRecordingProvider()
    split = await _bounded_reconcile_pipeline(split_provider, configured)._reconcile_bounded(
        inventory, over_core, rules, _reconcile_ledger(configured)
    )
    assert split_provider.calls == [(["first"], [], []), (["second"], [], [])]
    assert [fact.raw_value for fact in split.facts] == ["first", "second"]


@pytest.mark.asyncio
async def test_reconcile_unsplittable_atomic_item_fails_before_provider_io() -> None:
    inventory = DocumentInventoryOutput(
        documents=[{"document_key": "d", "document_type": "DECLARATION", "title": "Teilung"}]
    )
    core = CoreExtractionOutput(facts=[_reconcile_fact("too-large")])
    rules = RuleExtractionOutput(rules=[])
    cap = len(_serialize_provider_request("reconcile", inventory=inventory, core=core, rules=rules)) - 1
    configured = settings.model_copy(update={"declaration_provider_batch_chars": cap})
    provider = _ReconcileRecordingProvider()
    with pytest.raises(PipelineFailure, match="RECONCILE_PAYLOAD_LIMIT_EXCEEDED"):
        await _bounded_reconcile_pipeline(provider, configured)._reconcile_bounded(
            inventory, core, rules, _reconcile_ledger(configured)
        )
    assert provider.calls == []


class _SharedPipelineProvider:
    """Document-keyed fake with a controllable first-call barrier."""

    def __init__(self, first_key: str) -> None:
        self._first_key = first_key
        self.inventory_started = asyncio.Event()
        self.release_inventory = asyncio.Event()
        self.inventory_count = 0

    @staticmethod
    def _key(documents) -> str:
        return documents[0].document_key

    def _delegate(self, key: str) -> _RecordingProvider:
        usage = ProviderUsage(
            13 if key == self._first_key else 17,
            7,
            Decimal("0.000001"),
            1,
        )
        return _RecordingProvider(key, usage=usage)

    def estimate_reservation(self, method, *, max_output_tokens, **request):
        documents = request.get("documents")
        key = self._key(documents) if documents else "b"
        usage = 13 if key == self._first_key else 17
        return ProviderReservationEstimate(usage, 1, (usage,))

    async def inventory(self, documents, *, max_output_tokens):
        self.inventory_count += 1
        if self.inventory_count == 1:
            self.inventory_started.set()
            await self.release_inventory.wait()
        return await self._delegate(self._key(documents)).inventory(documents, max_output_tokens=max_output_tokens)

    async def extract_core(self, documents, *, max_output_tokens):
        return await self._delegate(self._key(documents)).extract_core(documents, max_output_tokens=max_output_tokens)

    async def extract_rules(self, documents, *, max_output_tokens):
        return await self._delegate(self._key(documents)).extract_rules(documents, max_output_tokens=max_output_tokens)

    async def reconcile(self, inventory, core, rules, *, max_output_tokens):
        key = inventory.documents[0].document_key
        return await self._delegate(key).reconcile(inventory, core, rules, max_output_tokens=max_output_tokens)

    async def recheck(self, documents, issue_codes, subject_keys, *, max_output_tokens):
        return await self._delegate(self._key(documents)).recheck(documents, issue_codes, subject_keys, max_output_tokens=max_output_tokens)


async def _seed_pipeline_import(
    seed_data: dict,
    pdf: bytes,
    *,
    organization_id: uuid.UUID | None = None,
    status: str = "QUEUED",
    mode: str = "NEW_PROPERTY",
    target_property_id: uuid.UUID | None = None,
) -> tuple[WorkerLease, str, _MemoryStore]:
    org_id = organization_id or seed_data["org_a_id"]
    initiated_by = (
        seed_data["user_a_admin_id"]
        if org_id == seed_data["org_a_id"]
        else seed_data["user_b_admin_id"]
    )
    import_id, document_id, token = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = f"memory/{document_id}.pdf"
    async with get_tenant_session(org_id) as session:
        session.add(
            DeclarationImport(
                id=import_id,
                organization_id=org_id,
                mode=mode,
                target_property_id=target_property_id,
                status="DRAFT",
                initiated_by=initiated_by,
                max_cost_usd=Decimal("1"),
                lease_owner="test-worker",
                lease_token=token,
                lease_generation=1,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            )
        )
        session.add(
            PropertyDocument(
                id=document_id,
                organization_id=org_id,
                doc_type="DECLARATION",
                title="Teilung",
                original_filename="teilung.pdf",
                mime_type="application/pdf",
                content_byte_size=len(pdf),
                content_sha256=compute_sha256(pdf),
                storage_uri=key,
                storage_backend="TEST",
                page_count=1,
                uploaded_by=initiated_by,
            )
        )
        await session.flush()
        session.add(
            DeclarationImportDocument(
                organization_id=org_id,
                import_id=import_id,
                document_id=document_id,
                sequence_no=1,
                document_role="DECLARATION",
            )
        )
        await session.flush()
        item = await session.get(DeclarationImport, import_id)
        request = await queue_fixture(session, item, initiated_by)
        item.status = status
        item.lease_owner = 'test-worker'
        item.lease_token = token
        item.lease_generation = 1
        item.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    return (
        WorkerLease(org_id, import_id, 1, token, datetime.now(timezone.utc) + timedelta(minutes=30), request),
        str(document_id),
        _MemoryStore({key: pdf}),
    )


@pytest.mark.asyncio
async def test_shared_pipeline_serializes_two_org_runs_without_cross_publication(seed_data) -> None:
    lease_a, key_a, store_a = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung A")
    )
    lease_b, key_b, store_b = await _seed_pipeline_import(
        seed_data,
        make_text_pdf("Teilungserklärung B"),
        organization_id=seed_data["org_b_id"],
    )
    provider = _SharedPipelineProvider(key_a)
    stores = {lease_a.organization_id: store_a, lease_b.organization_id: store_b}

    class _RoutingStore:
        def get(self, key):
            return stores[lease_a.organization_id].get(key) if key == next(iter(store_a._data)) else stores[lease_b.organization_id].get(key)

    pipeline = DeclarationPipeline(provider, store=_RoutingStore())
    first = asyncio.create_task(pipeline.run(lease_a))
    await asyncio.wait_for(provider.inventory_started.wait(), timeout=5)
    second = asyncio.create_task(pipeline.run(lease_b))
    await asyncio.sleep(0)
    assert provider.inventory_count == 1  # the second run cannot overwrite active lease/run state
    provider.release_inventory.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=20)

    async with get_tenant_session(lease_a.organization_id) as session:
        item_a = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease_a.import_id))
        candidates_a = await session.scalar(select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease_a.import_id))
    async with get_tenant_session(lease_b.organization_id) as session:
        item_b = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease_b.import_id))
        candidates_b = await session.scalar(select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease_b.import_id))
        foreign_a = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease_a.import_id))
    assert item_a is not None and item_a.status == "NEEDS_REVIEW" and item_a.input_tokens == 52
    assert item_b is not None and item_b.status == "NEEDS_REVIEW" and item_b.input_tokens == 68
    assert candidates_a and candidates_b
    assert foreign_a is None


@pytest.mark.asyncio
async def test_pipeline_publishes_real_tenant_graph_without_canonical_writes(seed_data) -> None:
    # Upload metadata starts at the safe placeholder of one page; preflight
    # owns the actual count and must publish both pages atomically.
    pdf = make_text_pdf("Teilungserklärung\nHauptstraße 1\nEinheit 1", "Fortsetzung")
    lease, document_key, store = await _seed_pipeline_import(seed_data, pdf)
    provider = _RecordingProvider(document_key)
    async with get_tenant_session(lease.organization_id) as session:
        before_properties = await session.scalar(select(func.count()).select_from(Property))
        before_units = await session.scalar(select(func.count()).select_from(Unit))
    await DeclarationPipeline(provider, store=store).run(lease)
    assert provider.calls == ["inventory", "extract_core", "extract_rules", "reconcile"]
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        run = await session.scalar(
            select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == lease.import_id)
        )
        document = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == uuid.UUID(document_key)))
        candidates = (await session.execute(select(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id))).scalars().all()
        evidence = await session.scalar(
            select(func.count()).select_from(DeclarationCandidateEvidence).join(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id)
        )
        issues = await session.scalar(
            select(func.count()).select_from(DeclarationCandidateIssue).where(DeclarationCandidateIssue.import_id == lease.import_id)
        )
        after_properties = await session.scalar(select(func.count()).select_from(Property))
        after_units = await session.scalar(select(func.count()).select_from(Unit))
    assert item is not None and item.status == "NEEDS_REVIEW" and item.progress_percent == 100
    assert item.input_tokens == 52 and item.output_tokens == 28 and item.attempt_count == 4
    assert run is not None and run.status == "SUCCEEDED" and run.input_tokens == 52
    assert document is not None and document.page_count == 2
    assert candidates and evidence and issues is not None
    assert (after_properties, after_units) == (before_properties, before_units)
    async with get_tenant_session(seed_data["org_b_id"]) as session:
        hidden = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        hidden_document = await session.scalar(
            select(PropertyDocument).where(PropertyDocument.id == uuid.UUID(document_key))
        )
        hidden_documents = await session.scalar(
            select(func.count()).select_from(DeclarationImportDocument).where(DeclarationImportDocument.import_id == lease.import_id)
        )
        hidden_runs = await session.scalar(
            select(func.count()).select_from(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == lease.import_id)
        )
        hidden_candidates = await session.scalar(
            select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id)
        )
        hidden_evidence = await session.scalar(
            select(func.count())
            .select_from(DeclarationCandidateEvidence)
            .join(DeclarationCandidate)
            .where(DeclarationCandidate.import_id == lease.import_id)
        )
        hidden_issues = await session.scalar(
            select(func.count()).select_from(DeclarationCandidateIssue).where(DeclarationCandidateIssue.import_id == lease.import_id)
        )
    assert hidden is None
    assert hidden_document is None
    assert (hidden_documents, hidden_runs, hidden_candidates, hidden_evidence, hidden_issues) == (0, 0, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["QUEUED", "RETRY_WAIT"])
async def test_pipeline_fails_over_limit_documents_before_storage_or_provider_io(
    seed_data, status: str
) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1"), status=status
    )
    second_document_id = uuid.uuid4()
    second_key = f"memory/{second_document_id}.pdf"
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(
            select(DeclarationImport).where(DeclarationImport.id == lease.import_id)
        )
        assert item is not None
        document = PropertyDocument(
            id=second_document_id,
            organization_id=lease.organization_id,
            doc_type="AMENDMENT",
            title="Nachtrag",
            original_filename="nachtrag.pdf",
            mime_type="application/pdf",
            content_byte_size=1,
            content_sha256="b" * 64,
            storage_uri=second_key,
            storage_backend="TEST",
            page_count=1,
            uploaded_by=item.initiated_by,
        )
        session.add(document)
        await session.flush()
        session.add(
            DeclarationImportDocument(
                organization_id=lease.organization_id,
                import_id=lease.import_id,
                document_id=document.id,
                sequence_no=2,
                document_role="AMENDMENT",
            )
        )

    class _NoReadStore(_MemoryStore):
        def get(self, key: str) -> bytes:
            raise AssertionError(f"storage must not be read for {key}")

    provider = _RecordingProvider(document_key)
    configured = settings.model_copy(update={"declaration_max_documents": 1})
    await DeclarationPipeline(provider, store=_NoReadStore(store._data), configured=configured).run(lease)

    assert provider.calls == []
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(
            select(DeclarationImport).where(DeclarationImport.id == lease.import_id)
        )
        assert item is not None
        assert item.status == "FAILED"
        assert item.error_code == "DOCUMENT_LIMIT_REACHED"
        assert item.next_attempt_at is None
        assert item.lease_owner is None
        assert item.lease_token is None


@pytest.mark.asyncio
async def test_reservation_is_committed_before_provider_and_failure_without_usage_keeps_it(seed_data) -> None:
    pdf = make_text_pdf("Teilungserklärung\nHauptstraße 1")
    lease, document_key, store = await _seed_pipeline_import(seed_data, pdf)
    probe = _ReservationProbeProvider(document_key, lease.organization_id, lease.import_id)
    await DeclarationPipeline(probe, store=store).run(lease)
    assert probe.before_inventory_tokens is not None and probe.before_inventory_tokens > 0
    failed_lease, failed_key, failed_store = await _seed_pipeline_import(seed_data, pdf)
    failing = _NoUsageFailureProvider(failed_key)
    await DeclarationPipeline(failing, store=failed_store).run(failed_lease)
    async with get_tenant_session(failed_lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == failed_lease.import_id))
        run = await session.scalar(select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == failed_lease.import_id))
    assert item is not None and item.status == "RETRY_WAIT"
    assert item.input_tokens > 0 and item.output_tokens > 0 and item.attempt_count == 1
    assert run is not None and run.status == "FAILED" and run.input_tokens == item.input_tokens


@pytest.mark.asyncio
async def test_depleted_recheck_output_budget_skips_estimator_and_persists_warning(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    provider = _BudgetSkippedRecheckProvider(document_key)
    configured = settings.model_copy(update={"declaration_max_output_tokens": 28})

    await DeclarationPipeline(provider, store=store, configured=configured).run(lease)

    assert provider.recheck_estimates == 0
    assert provider.calls == ["inventory", "extract_core", "extract_rules", "reconcile"]
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        warning = await session.scalar(
            select(DeclarationCandidateIssue)
            .where(DeclarationCandidateIssue.import_id == lease.import_id)
            .where(DeclarationCandidateIssue.code == "RECHECK_BUDGET_UNAVAILABLE")
        )
    assert item is not None and item.status == "NEEDS_REVIEW"
    assert warning is not None and warning.candidate_id is None


@pytest.mark.asyncio
async def test_high_confidence_quote_missing_recheck_uses_only_cited_page(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data,
        make_text_pdf("Teilungserklärung\nHauptstraße 1", "Nachbarseite", "Nicht erforderlich"),
    )
    provider = _CapturingRecheckProvider(document_key)
    provider.reconciliation_output.facts[0].evidence[0].quote = "nicht vorhandene Formulierung"
    await DeclarationPipeline(provider, store=store).run(lease)
    assert provider.calls == ["inventory", "extract_core", "extract_rules", "reconcile", "recheck"]
    assert provider.recheck_pages == [1]
    assert len(provider.recheck_images) == 1
    assert provider.recheck_images[0] is not None
    assert provider.recheck_images[0].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_vision_render_failure_never_calls_recheck_and_persists_page_blocker(seed_data) -> None:
    class _FailingVisionPreflight(PdfPreflight):
        def render_page_data_url(self, *_args, **_kwargs):
            raise DocumentIntegrityError("VISION_RENDER_TIMEOUT")

    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    provider = _CapturingRecheckProvider(document_key)
    provider.reconciliation_output.facts[0].evidence[0].quote = "nicht vorhandene Formulierung"

    await DeclarationPipeline(
        provider, store=store, preflight=_FailingVisionPreflight()
    ).run(lease)

    assert provider.calls == ["inventory", "extract_core", "extract_rules", "reconcile"]
    async with get_tenant_session(lease.organization_id) as session:
        issue = await session.scalar(
            select(DeclarationCandidateIssue)
            .where(DeclarationCandidateIssue.import_id == lease.import_id)
            .where(DeclarationCandidateIssue.code == "VISION_RENDER_UNAVAILABLE")
        )
    assert issue is not None
    assert issue.severity == "BLOCKER"
    assert issue.details_json == {"document_key": document_key, "pdf_page": 1}


@pytest.mark.asyncio
async def test_low_confidence_alone_does_not_trigger_recheck(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data,
        make_text_pdf("Teilungserklärung\nHauptstraße 1", "Nachbarseite"),
    )
    provider = _CapturingRecheckProvider(document_key)
    provider.reconciliation_output.facts[0].confidence = 0.1

    await DeclarationPipeline(provider, store=store).run(lease)

    assert provider.calls == ["inventory", "extract_core", "extract_rules", "reconcile"]
    assert provider.recheck_pages == []


@pytest.mark.asyncio
async def test_escape_heavy_recheck_batches_against_its_exact_metadata_envelope() -> None:
    class _EnvelopeRecordingProvider:
        def __init__(self) -> None:
            self.requests: list[tuple[tuple[DocumentInput, ...], tuple[str, ...], tuple[str, ...]]] = []

        def estimate_reservation(self, method, *, max_output_tokens, **request):
            assert method == "recheck"
            return ProviderReservationEstimate(1, 1, (1,))

        async def recheck(self, documents, issue_codes, subject_keys, *, max_output_tokens):
            self.requests.append((tuple(documents), tuple(issue_codes), tuple(subject_keys)))
            usage = ProviderUsage(1, 1, Decimal("0"), 1)
            metadata = _safe_attempt_metadata(pass_id="recheck", usage=usage)
            return ProviderCallResult(
                TargetedRecheckOutput(),
                usage,
                metadata=metadata,
                attempt_metadata=(metadata,),
            )

    configured = settings.model_copy(
        update={"declaration_provider_batch_chars": 4_000, "declaration_max_recheck_calls": 10}
    )
    provider = _EnvelopeRecordingProvider()
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    pipeline._provider = provider

    async def checkpoint(_ledger):
        return None

    pipeline._checkpoint = checkpoint
    issues = (
        ValidationIssue("LOW_CONFIDENCE", "BLOCKER", "safe", "property:new"),
        ValidationIssue("EVIDENCE_GAP", "WARNING", "safe", "unit:1"),
    )
    pages = (PageInput("doc", 1, ('"\\') * 800, 1.0, _VISION_DATA_URL),)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("100"), configured)

    await pipeline._call_rechecks(pages, issues, ledger)

    assert provider.requests
    assert all(
        len(
            _serialize_provider_request(
                "recheck",
                documents=documents,
                issue_codes=issue_codes,
                subject_keys=subject_keys,
            )
        )
        <= configured.declaration_provider_batch_chars
        for documents, issue_codes, subject_keys in provider.requests
    )


@pytest.mark.asyncio
async def test_segmented_vision_page_is_rejected_before_any_provider_call() -> None:
    class _NoCallProvider:
        def __init__(self) -> None:
            self.calls = 0

        def estimate_reservation(self, *_args, **_kwargs):
            raise AssertionError("split vision page must fail before estimating")

        async def recheck(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("split vision page must not reach provider")

    configured = settings.model_copy(
        update={"declaration_provider_batch_chars": 700, "declaration_max_recheck_calls": 10}
    )
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    pipeline._provider = _NoCallProvider()
    pipeline._checkpoint = lambda _ledger: asyncio.sleep(0)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("100"), configured)

    _output, remaining = await pipeline._call_rechecks(
        (PageInput("doc", 1, ('"\\') * 800, 1.0, _VISION_DATA_URL),), (), ledger
    )

    assert remaining is True
    assert pipeline._provider.calls == 0
    assert ledger.input_tokens == 0 and ledger.output_tokens == 0


@pytest.mark.asyncio
async def test_recheck_aggregate_budget_fence_prevents_all_provider_calls() -> None:
    class _Provider:
        def __init__(self) -> None:
            self.calls = 0

        def estimate_reservation(self, *_args, **_kwargs):
            return ProviderReservationEstimate(5, 1, (5,))

        async def recheck(self, *_args, **_kwargs):
            self.calls += 1
            usage = ProviderUsage(5, 1, Decimal("0"), 1)
            metadata = _safe_attempt_metadata(pass_id="recheck", usage=usage)
            return ProviderCallResult(
                TargetedRecheckOutput(), usage, metadata=metadata, attempt_metadata=(metadata,)
            )

    configured = settings.model_copy(
        update={"declaration_provider_batch_pages": 1, "declaration_max_recheck_calls": 2}
    )
    provider = _Provider()
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    pipeline._provider = provider
    pipeline._checkpoint = lambda _ledger: asyncio.sleep(0)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 7, 60_000, Decimal("100"), configured)
    pages = (
        PageInput("doc", 1, "first", 1.0, _VISION_DATA_URL),
        PageInput("doc", 2, "second", 1.0, _VISION_DATA_URL),
    )

    with pytest.raises(PipelineFailure, match="IMPORT_TOKEN_BUDGET_EXCEEDED"):
        await pipeline._call_rechecks(pages, (), ledger)

    assert provider.calls == 0
    assert ledger.input_tokens == 0 and ledger.output_tokens == 0 and ledger.attempts == 0


@pytest.mark.asyncio
async def test_recheck_aggregate_budget_fence_charges_then_settles_all_planned_calls() -> None:
    class _Provider:
        def __init__(self) -> None:
            self.calls = 0

        def estimate_reservation(self, *_args, **_kwargs):
            return ProviderReservationEstimate(5, 1, (5,))

        async def recheck(self, *_args, **_kwargs):
            self.calls += 1
            usage = ProviderUsage(5, 1, Decimal("0"), 1)
            metadata = _safe_attempt_metadata(pass_id="recheck", usage=usage)
            return ProviderCallResult(
                TargetedRecheckOutput(), usage, metadata=metadata, attempt_metadata=(metadata,)
            )

    configured = settings.model_copy(
        update={"declaration_provider_batch_pages": 1, "declaration_max_recheck_calls": 2}
    )
    provider = _Provider()
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    pipeline._provider = provider
    pipeline._checkpoint = lambda _ledger: asyncio.sleep(0)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 20, 60_000, Decimal("100"), configured)
    pages = (
        PageInput("doc", 1, "first", 1.0, _VISION_DATA_URL),
        PageInput("doc", 2, "second", 1.0, _VISION_DATA_URL),
    )

    _output, remaining = await pipeline._call_rechecks(pages, (), ledger)

    assert remaining is False and provider.calls == 2
    assert ledger.input_tokens == 10 and ledger.output_tokens == 2 and ledger.attempts == 2


@pytest.mark.asyncio
async def test_recheck_failure_charges_and_checkpoints_only_the_executed_batch() -> None:
    class _FirstBatchFailureProvider:
        def __init__(self) -> None:
            self.calls = 0

        def estimate_reservation(self, *_args, **_kwargs):
            return ProviderReservationEstimate(5, 1, (5,))

        async def recheck(self, *_args, **_kwargs):
            self.calls += 1
            usage = ProviderUsage(5, 1, Decimal("0"), 1)
            metadata = _safe_attempt_metadata(
                pass_id="recheck", failure_class="PROVIDER_TIMEOUT", usage=usage
            )
            raise ProviderFailure(
                "PROVIDER_TIMEOUT",
                True,
                "safe",
                usage=usage,
                attempt_metadata=(metadata,),
            )

    configured = settings.model_copy(
        update={"declaration_provider_batch_pages": 1, "declaration_max_recheck_calls": 2}
    )
    provider = _FirstBatchFailureProvider()
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    pipeline._provider = provider
    checkpoints: list[tuple[int, int, int, int]] = []

    async def checkpoint(current_ledger):
        checkpoints.append(
            (
                current_ledger.input_tokens,
                current_ledger.output_tokens,
                current_ledger.attempts,
                len(current_ledger.pass_entries),
            )
        )

    pipeline._checkpoint = checkpoint
    ledger = _Ledger(0, 0, Decimal("0"), 0, 20, 60_000, Decimal("100"), configured)
    pages = (
        PageInput("doc", 1, "first", 1.0, _VISION_DATA_URL),
        PageInput("doc", 2, "second", 1.0, _VISION_DATA_URL),
    )

    with pytest.raises(ProviderFailure, match="safe"):
        await pipeline._call_rechecks(pages, (), ledger)

    assert provider.calls == 1
    assert ledger.input_tokens == 5
    assert ledger.output_tokens == 1
    assert ledger.attempts == 1
    assert [
        (
            entry.as_safe_dict()["pass_id"],
            entry.as_safe_dict()["attempt_no"],
            entry.as_safe_dict()["failure_class"],
        )
        for entry in ledger.pass_entries
    ] == [("recheck", 1, "PROVIDER_TIMEOUT")]
    assert checkpoints == [(5, configured.declaration_provider_max_output_tokens_per_call, 1, 0), (5, 1, 1, 1)]


@pytest.mark.asyncio
async def test_recheck_metadata_that_cannot_fit_fails_before_provider_io() -> None:
    class _NoCallProvider:
        def estimate_reservation(self, *_args, **_kwargs):
            raise AssertionError("metadata must fail before a reservation")

        async def recheck(self, *_args, **_kwargs):
            raise AssertionError("metadata must fail before provider I/O")

    issue = ValidationIssue("X" * 1_000, "BLOCKER", "safe", "property:new")
    cap = len(
        _serialize_provider_request(
            "recheck", documents=(), issue_codes=(issue.code,), subject_keys=(issue.subject_key,)
        )
    ) - 1
    configured = settings.model_copy(update={"declaration_provider_batch_chars": cap})
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    pipeline._provider = _NoCallProvider()

    async def checkpoint(_ledger):
        return None

    pipeline._checkpoint = checkpoint
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("100"), configured)
    with pytest.raises(PipelineFailure, match="PROVIDER_REQUEST_CHAR_LIMIT_EXCEEDED"):
        await pipeline._call_rechecks(
            (PageInput("doc", 1, "text", 1.0),), (issue,), ledger
        )


@pytest.mark.asyncio
async def test_candidate_cap_fails_without_publishing_a_graph(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    await DeclarationPipeline(
        _RecordingProvider(document_key),
        store=store,
        configured=settings.model_copy(update={"declaration_max_candidates": 0}),
    ).run(lease)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        count = await session.scalar(select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id))
    assert item is not None and item.status == "FAILED" and item.error_code == "CANDIDATE_LIMIT_EXCEEDED"
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type,error_code", [(_BareResultProvider, "PROVIDER_RESULT_INVALID"), (_OverReservationProvider, "PROVIDER_USAGE_EXCEEDED_RESERVATION")])
async def test_invalid_or_over_reservation_result_stops_before_later_calls(seed_data, provider_type, error_code) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    provider = provider_type(document_key)
    await DeclarationPipeline(provider, store=store).run(lease)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert provider.calls == ["inventory"]
    assert item is not None and item.status == "FAILED" and item.error_code == error_code
    if error_code == "PROVIDER_USAGE_EXCEEDED_RESERVATION":
        assert item.input_tokens == 14


@pytest.mark.asyncio
async def test_wrong_typed_provider_result_persists_reservation_and_fails_before_graph_or_later_call(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    configured = settings.model_copy(
        update={"declaration_provider_max_output_tokens_per_call": 7}
    )
    provider = _WrongTypedResultProvider(document_key)
    await DeclarationPipeline(provider, store=store, configured=configured).run(lease)

    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        run = await session.scalar(
            select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == lease.import_id)
        )
        candidates = await session.scalar(
            select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id)
        )
        evidence = await session.scalar(
            select(func.count())
            .select_from(DeclarationCandidateEvidence)
            .join(DeclarationCandidate)
            .where(DeclarationCandidate.import_id == lease.import_id)
        )
        issues = await session.scalar(
            select(func.count()).select_from(DeclarationCandidateIssue).where(DeclarationCandidateIssue.import_id == lease.import_id)
        )
    assert provider.calls == ["inventory"]
    assert item is not None
    assert (item.status, item.error_code, item.input_tokens, item.output_tokens, item.attempt_count) == (
        "FAILED", "PROVIDER_RESULT_INVALID", 13, 7, 1
    )
    assert run is not None and run.status == "FAILED" and run.input_tokens == 13 and run.output_tokens == 7
    assert (candidates, evidence, issues) == (0, 0, 0)


@pytest.mark.asyncio
async def test_multi_batch_fixed_pass_order_covers_every_page_without_canonical_mutation(seed_data) -> None:
    pages = tuple(f"Seite {number}" for number in range(1, 6))
    lease, document_key, store = await _seed_pipeline_import(seed_data, make_text_pdf(*pages))
    configured = settings.model_copy(
        update={
            "declaration_provider_batch_pages": 2,
            "declaration_provider_batch_chars": 80_000,
        }
    )
    provider = _DetailedBatchProvider(document_key, evidence_quote="Seite")
    async with get_tenant_session(lease.organization_id) as session:
        before_properties = await session.scalar(select(func.count()).select_from(Property))
        before_units = await session.scalar(select(func.count()).select_from(Unit))

    await DeclarationPipeline(provider, store=store, configured=configured).run(lease)

    by_method = {
        method: [page_numbers for recorded_method, page_numbers in provider.call_pages if recorded_method == method]
        for method in ("inventory", "extract_core", "extract_rules")
    }
    expected_batches = [(1, 2), (2, 3), (3, 4), (4, 5)]
    assert by_method == {
        "inventory": expected_batches,
        "extract_core": expected_batches,
        "extract_rules": expected_batches,
    }
    assert [method for method, _pages in provider.call_pages] == [
        "inventory", "inventory", "inventory", "inventory",
        "extract_core", "extract_core", "extract_core", "extract_core",
        "extract_rules", "extract_rules", "extract_rules", "extract_rules",
        "reconcile",
    ]
    assert all(
        current[-1] == following[0]
        for batches in by_method.values()
        for current, following in zip(batches, batches[1:])
    )
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        document = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == uuid.UUID(document_key)))
        after_properties = await session.scalar(select(func.count()).select_from(Property))
        after_units = await session.scalar(select(func.count()).select_from(Unit))
    assert item is not None and item.status == "NEEDS_REVIEW"
    assert document is not None and document.page_count == 5
    assert (after_properties, after_units) == (before_properties, before_units)


@pytest.mark.asyncio
async def test_escape_heavy_oversized_page_is_envelope_batched_before_all_fixed_passes(seed_data) -> None:
    source = ('"\\\\') * 900
    configured = settings.model_copy(
        update={
            "declaration_provider_batch_pages": 20,
            "declaration_provider_batch_chars": 900,
        }
    )
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = configured
    page = PageInput("doc", 1, source, 1.0)
    batches = pipeline._page_batches((page,))
    unique_segments = {
        (item.segment_no, item.segment_count): item
        for batch in batches
        for item in batch.pages
    }
    assert [(item.segment_no, item.segment_count) for item in unique_segments.values()] == sorted(unique_segments)
    assert "".join(
        item.text.split("\n", 1)[1]
        for _key, item in sorted(unique_segments.items())
    ) == source
    assert all(
        len(_serialize_provider_request(method, documents=batch.as_documents())) <= configured.declaration_provider_batch_chars
        for batch in batches
        for method in ("inventory", "extract_core", "extract_rules")
    )

    lease, document_key, store = await _seed_pipeline_import(seed_data, make_text_pdf(source))
    provider = _DetailedBatchProvider(document_key, evidence_quote='"')
    await DeclarationPipeline(provider, store=store, configured=configured).run(lease)

    assert provider.calls[-1] == "reconcile"
    assert provider.serialized_request_sizes
    assert all(
        size <= configured.declaration_provider_batch_chars
        for method, size in provider.serialized_request_sizes
        if method in {"inventory", "extract_core", "extract_rules"}
    )
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert item is not None and item.status == "NEEDS_REVIEW"


@pytest.mark.asyncio
async def test_provider_failure_usage_is_settled_once(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    provider = _AttachedUsageFailureProvider(document_key)
    await DeclarationPipeline(provider, store=store).run(lease)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert provider.calls == ["inventory"]
    assert item is not None and item.status == "FAILED" and item.input_tokens == 5 and item.output_tokens == 2 and item.attempt_count == 1


@pytest.mark.asyncio
async def test_integrity_failure_happens_before_provider_call(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    storage_key = next(iter(store._data))
    store._data[storage_key] = b"not the recorded PDF"
    provider = _RecordingProvider(document_key)
    await DeclarationPipeline(provider, store=store).run(lease)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert provider.calls == []
    assert item is not None and item.status == "FAILED" and item.error_code == "STORAGE_INTEGRITY_MISMATCH"


@pytest.mark.asyncio
async def test_known_out_of_range_evidence_is_bound_blocker_but_unknown_evidence_stops(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    await DeclarationPipeline(_OutOfRangeRelationProvider(document_key), store=store).run(lease)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        issue = await session.scalar(
            select(DeclarationCandidateIssue)
            .where(DeclarationCandidateIssue.import_id == lease.import_id)
            .where(DeclarationCandidateIssue.code == "EVIDENCE_PAGE_OUT_OF_RANGE")
        )
    assert item is not None and item.status == "NEEDS_REVIEW"
    assert issue is not None and issue.candidate_id is not None

    unknown_lease, unknown_key, unknown_store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    await DeclarationPipeline(_UnknownEvidenceProvider(unknown_key), store=unknown_store).run(unknown_lease)
    async with get_tenant_session(unknown_lease.organization_id) as session:
        failed = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == unknown_lease.import_id))
        candidates = await session.scalar(select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == unknown_lease.import_id))
    assert failed is not None and failed.status == "FAILED" and failed.error_code == "UNKNOWN_EVIDENCE_DOCUMENT"
    assert candidates == 0


@pytest.mark.asyncio
async def test_invented_quote_is_bound_as_a_candidate_blocker_before_review(seed_data) -> None:
    class _InventedQuoteProvider(_RecordingProvider):
        def __init__(self, document_key: str) -> None:
            super().__init__(document_key)
            self.reconciliation_output.facts[0].evidence[0].quote = (
                "nicht vorhandene erfundene Formulierung"
            )

    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )

    await DeclarationPipeline(_InventedQuoteProvider(document_key), store=store).run(lease)

    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(
            select(DeclarationImport).where(DeclarationImport.id == lease.import_id)
        )
        blockers = (
            await session.execute(
                select(DeclarationCandidateIssue)
                .where(DeclarationCandidateIssue.import_id == lease.import_id)
                .where(DeclarationCandidateIssue.code == "EVIDENCE_QUOTE_NOT_FOUND")
            )
        ).scalars().all()

    assert item is not None and item.status == "NEEDS_REVIEW"
    assert blockers
    assert all(issue.candidate_id is not None for issue in blockers)


@pytest.mark.asyncio
async def test_invented_inventory_relation_quote_remains_bound_after_recheck(seed_data) -> None:
    class _InventedInventoryRelationProvider(_RecordingProvider):
        def __init__(self, document_key: str) -> None:
            super().__init__(document_key)
            self.inventory_output = DocumentInventoryOutput(
                documents=[
                    {
                        "document_key": document_key,
                        "document_type": "DECLARATION",
                        "title": "Teilung",
                        "relations": [
                            {
                                "relation_type": "SUPPLEMENTS",
                                "target_document_key": document_key,
                                "evidence": [
                                    {
                                        "document_key": document_key,
                                        "pdf_page": 1,
                                        "quote": "nicht vorhandene Inventarformulierung",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            )

    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    provider = _InventedInventoryRelationProvider(document_key)

    await DeclarationPipeline(provider, store=store).run(lease)

    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(
            select(DeclarationImport).where(DeclarationImport.id == lease.import_id)
        )
        blockers = (
            await session.execute(
                select(DeclarationCandidateIssue)
                .where(DeclarationCandidateIssue.import_id == lease.import_id)
                .where(DeclarationCandidateIssue.code == "EVIDENCE_QUOTE_NOT_FOUND")
            )
        ).scalars().all()

    assert provider.calls == [
        "inventory",
        "extract_core",
        "extract_rules",
        "reconcile",
        "recheck",
    ]
    assert item is not None and item.status == "NEEDS_REVIEW"
    assert len(blockers) == 1
    assert blockers[0].candidate_id is not None


@pytest.mark.asyncio
async def test_due_retry_wait_lease_restarts_and_clears_retry_metadata(seed_data) -> None:
    lease, document_key, store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        assert item is not None
        item.status = "RETRY_WAIT"
        item.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        item.error_code = "PROVIDER_TIMEOUT"
        item.error_message = "PROVIDER_TIMEOUT"
    await DeclarationPipeline(_RecordingProvider(document_key), store=store).run(lease)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert item is not None and item.status == "NEEDS_REVIEW"
    assert item.next_attempt_at is None and item.error_code is None and item.error_message is None


def _draft(stable_key: str, document_id: uuid.UUID) -> DraftCandidate:
    return DraftCandidate(
        stable_key=stable_key,
        candidate_type="PROPERTY_FIELD",
        subject_key="property:main",
        field_key="street",
        operation="CREATE",
        previous_value_json=None,
        proposed_value_json={"kind": "TEXT", "value": "Hauptstraße 1"},
        confidence=Decimal("0.99"),
        risk_class="CRITICAL",
        requires_individual_review=True,
        evidence=(DraftEvidence(document_id, 1, None, None, "Beleg"),),
    )


@pytest.mark.asyncio
async def test_stale_start_checkpoint_and_failure_cannot_mutate_real_rows(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        assert item is not None
        item.status = "PREFLIGHT"
        item.lease_token = uuid.uuid4()
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings
    pipeline._provider = object()
    pipeline._tenant_session_factory = get_tenant_session
    pipeline._active_lease = lease
    pipeline._active_run_id = None
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._start_run(lease)
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._checkpoint(ledger)
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._fail(lease, "TEST_FAILURE", retryable=False, ledger=ledger)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        runs = await session.scalar(
            select(func.count()).select_from(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == lease.import_id)
        )
    assert item is not None and item.status == "PREFLIGHT" and item.error_code is None
    assert runs == 0


@pytest.mark.asyncio
async def test_cancel_closes_running_run_in_same_real_transaction(seed_data) -> None:
    lease, _document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        assert item is not None
        session.add(
            DeclarationExtractionRun(
                organization_id=lease.organization_id,
                import_id=lease.import_id,
                status="RUNNING",
                provider="test",
                model_name="test",
                prompt_version="test",
                schema_version="test",
                harness_version="test",
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
            )
        )
        await session.flush()
        await DeclarationImportService(session, lease.organization_id, seed_data["user_a_admin_id"]).cancel(lease.import_id)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        run = await session.scalar(select(DeclarationExtractionRun).where(DeclarationExtractionRun.import_id == lease.import_id))
    assert item is not None and item.status == "CANCELLED"
    assert run is not None and run.status == "FAILED" and run.error_code == "IMPORT_CANCELLED" and run.completed_at is not None


@pytest.mark.asyncio
async def test_real_graph_replacement_rolls_back_on_fk_error_and_reviewed_collision(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    document_id = uuid.UUID(document_key)
    async with get_tenant_session(lease.organization_id) as session:
        await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
            lease.import_id,
            (_draft("candidate:prior", document_id),),
            (
                DraftIssue("PRIOR", "BLOCKER", "prior graph", "candidate:prior"),
                DraftIssue("PRIOR_IMPORT", "WARNING", "prior import issue", None),
            ),
        )
    with pytest.raises(IntegrityError):
        async with get_tenant_session(lease.organization_id) as session:
            await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
                lease.import_id,
                (_draft("candidate:fk", uuid.uuid4()),),
                (),
            )
    async with get_tenant_session(lease.organization_id) as session:
        candidates = (await session.execute(
            select(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id)
        )).scalars().all()
        evidence = (await session.execute(
            select(DeclarationCandidateEvidence)
            .join(DeclarationCandidate)
            .where(DeclarationCandidate.import_id == lease.import_id)
        )).scalars().all()
        issues = (await session.execute(
            select(DeclarationCandidateIssue).where(DeclarationCandidateIssue.import_id == lease.import_id)
        )).scalars().all()
    assert [item.stable_key for item in candidates] == ["candidate:prior"]
    assert [item.quote for item in evidence] == ["Beleg"]
    assert [
        (item.code, item.message, item.candidate_id is None)
        for item in issues
    ] == [
        ("PRIOR", "prior graph", False),
        ("PRIOR_IMPORT", "prior import issue", True),
    ]
    async with get_tenant_session(lease.organization_id) as session:
        session.add(
            DeclarationCandidate(
                organization_id=lease.organization_id,
                import_id=lease.import_id,
                stable_key="candidate:protected",
                candidate_type="PROPERTY_FIELD",
                subject_key="property:main",
                field_key="street",
                operation="CREATE",
                confidence=Decimal("0.99"),
                risk_class="CRITICAL",
                requires_individual_review=True,
                review_status="ACCEPTED",
                reviewed_by=seed_data["user_a_admin_id"],
                reviewed_at=datetime.now(timezone.utc),
            )
        )
    with pytest.raises(ValueError, match="PROTECTED_REVIEWED_CANDIDATE_COLLISION"):
        async with get_tenant_session(lease.organization_id) as session:
            await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
                lease.import_id,
                (_draft("candidate:protected", document_id),),
                (),
            )
    async with get_tenant_session(lease.organization_id) as session:
        protected = await session.scalar(
            select(DeclarationCandidate)
            .where(DeclarationCandidate.import_id == lease.import_id)
            .where(DeclarationCandidate.stable_key == "candidate:protected")
        )
    assert protected is not None and protected.review_status == "ACCEPTED"


@pytest.mark.asyncio
async def test_final_cas_miss_after_graph_insert_rolls_back_real_transaction(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    document_id = uuid.UUID(document_key)
    stale = lease
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        assert item is not None
        item.status = "RECONCILING"
        item.lease_token = uuid.uuid4()
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        async with get_tenant_session(lease.organization_id) as session:
            candidate_repo = DeclarationCandidateRepository(session, lease.organization_id)
            import_repo = DeclarationImportRepository(session, lease.organization_id)
            await candidate_repo.replace_for_import(
                lease.import_id,
                (_draft("candidate:cas", document_id),),
                (DraftIssue("TEST", "BLOCKER", "safe", "candidate:cas"),),
            )
            if not await import_repo.transition_with_lease(
                stale,
                {"RECONCILING"},
                "NEEDS_REVIEW",
                {"current_stage": "review"},
            ):
                raise PipelineFailure("LEASE_LOST")
    async with get_tenant_session(lease.organization_id) as session:
        candidates = await session.scalar(select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id))
        evidence = await session.scalar(
            select(func.count()).select_from(DeclarationCandidateEvidence).join(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id)
        )
        issues = await session.scalar(
            select(func.count()).select_from(DeclarationCandidateIssue).where(DeclarationCandidateIssue.import_id == lease.import_id)
        )
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert (candidates, evidence, issues) == (0, 0, 0)
    assert item is not None and item.status == "RECONCILING"


@pytest.mark.asyncio
async def test_stale_publish_leaves_graph_page_count_and_run_unchanged(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    document_id = uuid.UUID(document_key)
    async with get_tenant_session(lease.organization_id) as session:
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        assert item is not None
        item.status = "RECONCILING"
        item.lease_token = uuid.uuid4()
        run = DeclarationExtractionRun(
            organization_id=lease.organization_id,
            import_id=lease.import_id,
            status="RUNNING",
            provider="test",
            model_name="test",
            prompt_version="test",
            schema_version="test",
            harness_version="test",
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=Decimal("0"),
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings
    pipeline._tenant_session_factory = get_tenant_session
    pipeline._active_run_id = run_id
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)
    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._publish(lease, {document_key: 9}, (_draft("candidate:stale", document_id),), (), ledger)
    async with get_tenant_session(lease.organization_id) as session:
        candidates = await session.scalar(select(func.count()).select_from(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id))
        document = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == document_id))
        run = await session.scalar(select(DeclarationExtractionRun).where(DeclarationExtractionRun.id == run_id))
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert candidates == 0
    assert document is not None and document.page_count == 1
    assert run is not None and run.status == "RUNNING" and run.completed_at is None
    assert item is not None and item.status == "RECONCILING"


@pytest.mark.asyncio
async def test_publish_final_cas_miss_rolls_back_graph_page_count_run_and_import(seed_data, monkeypatch) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    document_id = uuid.UUID(document_key)
    async with get_tenant_session(lease.organization_id) as session:
        await DeclarationCandidateRepository(session, lease.organization_id).replace_for_import(
            lease.import_id,
            (_draft("candidate:prior", document_id),),
            (
                DraftIssue("PRIOR", "BLOCKER", "prior graph", "candidate:prior"),
                DraftIssue("PRIOR_IMPORT", "WARNING", "prior import issue", None),
            ),
        )
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
        assert item is not None
        item.status = "RECONCILING"
        run = DeclarationExtractionRun(
            organization_id=lease.organization_id,
            import_id=lease.import_id,
            status="RUNNING",
            provider="test",
            model_name="test",
            prompt_version="test",
            schema_version="test",
            harness_version="test",
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=Decimal("0"),
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    class _FinalCasMissRepository(DeclarationImportRepository):
        async def transition_with_lease(self, lease, expected, status, values):
            if status == "NEEDS_REVIEW":
                return False
            return await super().transition_with_lease(lease, expected, status, values)

    monkeypatch.setattr(pipeline_module, "DeclarationImportRepository", _FinalCasMissRepository)
    pipeline = object.__new__(DeclarationPipeline)
    pipeline._settings = settings
    pipeline._tenant_session_factory = get_tenant_session
    pipeline._active_run_id = run_id
    ledger = _Ledger(0, 0, Decimal("0"), 0, 500_000, 60_000, Decimal("1"), settings)

    with pytest.raises(PipelineFailure, match="LEASE_LOST"):
        await pipeline._publish(
            lease,
            {document_key: 9},
            (_draft("candidate:new", document_id),),
            (DraftIssue("NEW", "BLOCKER", "new graph", "candidate:new"),),
            ledger,
        )

    async with get_tenant_session(lease.organization_id) as session:
        candidates = (await session.execute(
            select(DeclarationCandidate).where(DeclarationCandidate.import_id == lease.import_id)
        )).scalars().all()
        evidence = (await session.execute(
            select(DeclarationCandidateEvidence)
            .join(DeclarationCandidate)
            .where(DeclarationCandidate.import_id == lease.import_id)
        )).scalars().all()
        issues = (await session.execute(
            select(DeclarationCandidateIssue).where(DeclarationCandidateIssue.import_id == lease.import_id)
        )).scalars().all()
        document = await session.scalar(select(PropertyDocument).where(PropertyDocument.id == document_id))
        run = await session.scalar(select(DeclarationExtractionRun).where(DeclarationExtractionRun.id == run_id))
        item = await session.scalar(select(DeclarationImport).where(DeclarationImport.id == lease.import_id))
    assert [candidate.stable_key for candidate in candidates] == ["candidate:prior"]
    assert [item.quote for item in evidence] == ["Beleg"]
    assert {
        (issue.code, issue.message, issue.candidate_id is None)
        for issue in issues
    } == {
        ("PRIOR", "prior graph", False),
        ("PRIOR_IMPORT", "prior import issue", True),
    }
    assert document is not None and document.page_count == 1
    assert run is not None and run.status == "RUNNING" and run.completed_at is None
    assert item is not None and item.status == "RECONCILING"


@pytest.mark.asyncio
async def test_existing_property_conversion_uses_real_decimal_mea_and_bound_blockers(seed_data) -> None:
    lease, document_key, _store = await _seed_pipeline_import(
        seed_data, make_text_pdf("Teilungserklärung\nHauptstraße 1")
    )
    async with get_tenant_session(lease.organization_id) as session:
        property_row = Property(
            organization_id=lease.organization_id,
            name="Haus",
            street="Hauptstraße 1",
            zip_code="40213",
            city="Düsseldorf",
            management_type="WEG",
            mea_denominator=100,
            mea_scale_status="CONFIRMED",
        )
        session.add(property_row)
        await session.flush()
        unit = Unit(
            organization_id=lease.organization_id,
            property_id=property_row.id,
            number="1",
            location="EG",
            usage_type="WOHNUNG",
            area=Decimal("50.00"),
            heated_area=Decimal("45.00"),
            mea=Decimal("100"),
        )
        session.add(unit)
        await session.flush()
        property_id, unit_id = property_row.id, unit.id
    evidence = [{"document_key": document_key, "pdf_page": 1, "quote": "Beleg"}]
    facts = [
        FactPayload(subject_key="property:main", field="street", raw_value="Hauptstraße 1", normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"), confidence=.99, evidence=evidence),
        FactPayload(subject_key="property:main", field="mea_denominator", raw_value="100", normalized_value=IntegerValue(kind="INTEGER", value=100), confidence=.99, evidence=evidence),
        FactPayload(subject_key="unit:one", field="number", raw_value="1", normalized_value=TextValue(kind="TEXT", value="1"), confidence=.99, evidence=evidence),
        FactPayload(subject_key="unit:one", field="area", raw_value="50", normalized_value=DecimalValue(kind="DECIMAL", value="50", unit="m²"), confidence=.99, evidence=evidence),
        FactPayload(subject_key="unit:one", field="heated_area", raw_value="46", normalized_value=DecimalValue(kind="DECIMAL", value="46", unit="m²"), confidence=.99, evidence=evidence),
        FactPayload(subject_key="unit:one", field="mea", raw_value="100/100", normalized_value=MeaFraction(kind="MEA", numerator=100, denominator=100), confidence=.99, evidence=evidence),
    ]
    imported = SimpleNamespace(mode="EXISTING_PROPERTY", target_property_id=property_id)
    inventory = DocumentInventoryOutput(documents=[{"document_key": document_key, "document_type": "DECLARATION", "title": "Teilung"}])
    output = ReconciliationOutput(facts=facts, rules=[], mea_set_status="COMPLETE")
    drafts, issues = _candidate_drafts(imported, inventory, output, (), {document_key: uuid.UUID(document_key)}, property_row, [unit])
    by_key = {item.stable_key: item for item in drafts}
    assert by_key["fact:property:main:street"].operation == "KEEP"
    assert by_key["fact:unit:one:area"].operation == "KEEP"
    assert by_key["fact:unit:one:heated_area"].operation == "UPDATE"
    assert by_key["fact:unit:one:mea"].operation == "KEEP"
    assert all(item.operation != "DELETE" for item in drafts)
    rescale_facts = [*facts]
    rescale_facts[1] = FactPayload(subject_key="property:main", field="mea_denominator", raw_value="200", normalized_value=IntegerValue(kind="INTEGER", value=200), confidence=.88, evidence=evidence)
    rescale_facts[-1] = FactPayload(subject_key="unit:one", field="mea", raw_value="100/200", normalized_value=MeaFraction(kind="MEA", numerator=100, denominator=200), confidence=.88, evidence=evidence)
    rescale, _ = _candidate_drafts(imported, inventory, ReconciliationOutput(facts=rescale_facts, rules=[], mea_set_status="PARTIAL"), (), {document_key: uuid.UUID(document_key)}, property_row, [unit])
    rescale_candidate = next(item for item in rescale if item.candidate_type == "MEA_RESCALE")
    assert rescale_candidate.evidence and rescale_candidate.confidence == Decimal("0.88")
    property_row.mea_scale_status = "LEGACY_UNCONFIRMED"
    _drafts, blockers = _candidate_drafts(imported, inventory, output, (), {document_key: uuid.UUID(document_key)}, property_row, [unit])
    assert any(item.code == "MEA_CANONICAL_SCALE_UNCONFIRMED" and item.candidate_stable_key for item in blockers)
