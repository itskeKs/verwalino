"""Privacy-safe observability contracts for declaration provider passes."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.declarations.observability import (
    PassLedgerEntry,
    ProviderPassMetadata,
    fingerprint_static_contract,
)
from app.declarations.provider import (
    PROVIDER_INCOMPLETE,
    PROVIDER_REFUSAL,
    PROVIDER_SCHEMA_FAILURE,
    PROVIDER_TIMEOUT,
)


def _metadata_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "pass_id": "inventory",
        "attempt_no": 1,
        "stage": "provider",
        "configured_model": "gpt-5.6-luna",
        "returned_model_id": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "request_id": "req_safe_0001",
        "prompt_version": "declaration-v1",
        "prompt_sha256": "a" * 64,
        "schema_version": "1",
        "schema_sha256": "b" * 64,
        "harness_version": "1",
        "harness_sha256": "c" * 64,
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 10,
        "output_tokens": 25,
        "estimated_cost_usd": Decimal("0.0000125"),
        "latency_ms": 42,
        "retry_count": 0,
        "route_action": "NONE",
        "route_reason_codes": (),
        "failure_class": None,
    }
    values.update(overrides)
    return values


def test_pass_metadata_rejects_document_content_fields() -> None:
    for field in (
        "quote",
        "prompt",
        "document_key",
        "image_data_url",
        "response_body",
        "tenant_id",
        "path",
        "exception_message",
    ):
        with pytest.raises(ValueError) as exc_info:
            ProviderPassMetadata.from_values(**_metadata_values(**{field: "secret"}))
        assert "secret" not in str(exc_info.value)


def test_safe_ledger_payload_contains_only_allowlisted_fields() -> None:
    metadata = ProviderPassMetadata.from_values(**_metadata_values())
    ledger = PassLedgerEntry.from_metadata(metadata)

    with pytest.raises(FrozenInstanceError):
        ledger.stage = "recheck"  # type: ignore[misc]

    assert ledger.as_safe_dict() == {
        "pass_id": "inventory",
        "attempt_no": 1,
        "stage": "provider",
        "configured_model": "gpt-5.6-luna",
        "returned_model_id": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "request_id": "req_safe_0001",
        "prompt_version": "declaration-v1",
        "prompt_sha256": "a" * 64,
        "schema_version": "1",
        "schema_sha256": "b" * 64,
        "harness_version": "1",
        "harness_sha256": "c" * 64,
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 10,
        "output_tokens": 25,
        "estimated_cost_usd": "0.0000125",
        "latency_ms": 42,
        "retry_count": 0,
        "route_action": "NONE",
        "route_reason_codes": [],
        "failure_class": None,
    }


def test_independent_prompt_schema_harness_fingerprints() -> None:
    first = fingerprint_static_contract(
        developer_prompt="static instruction",
        schema={"type": "object", "properties": {"x": {"type": "string"}}},
        harness="static harness",
    )
    changed_prompt = fingerprint_static_contract(
        developer_prompt="changed instruction",
        schema={"type": "object", "properties": {"x": {"type": "string"}}},
        harness="static harness",
    )
    reordered_schema = fingerprint_static_contract(
        developer_prompt="static instruction",
        schema={"properties": {"x": {"type": "string"}}, "type": "object"},
        harness="static harness",
    )

    assert first.schema_sha256 == reordered_schema.schema_sha256
    assert first.prompt_sha256 != changed_prompt.prompt_sha256
    assert first.schema_sha256 == changed_prompt.schema_sha256
    assert first.harness_sha256 == changed_prompt.harness_sha256


def test_provider_failure_classes_are_distinct() -> None:
    assert len(
        {PROVIDER_REFUSAL, PROVIDER_INCOMPLETE, PROVIDER_TIMEOUT, PROVIDER_SCHEMA_FAILURE}
    ) == 4


@pytest.mark.parametrize(
    "reason_code",
    [
        PROVIDER_REFUSAL,
        PROVIDER_INCOMPLETE,
        PROVIDER_TIMEOUT,
        PROVIDER_SCHEMA_FAILURE,
        "UNKNOWN_EVIDENCE_DOCUMENT",
        "EVIDENCE_PAGE_OUT_OF_RANGE",
        "EVIDENCE_QUOTE_NOT_FOUND",
        "LOW_PAGE_QUALITY",
        "DOMAIN_BLOCKER",
    ],
)
def test_route_reason_codes_accept_stable_source_codes(reason_code: str) -> None:
    metadata = ProviderPassMetadata(**_metadata_values(route_reason_codes=(reason_code,)))

    assert metadata.route_reason_codes == (reason_code,)


@pytest.mark.parametrize(
    "reason_code", ["MISSING_QUOTE", "UNKNOWN_EVIDENCE", "PROVIDER_FAILURE"]
)
def test_route_reason_codes_reject_lossy_generic_codes(reason_code: str) -> None:
    with pytest.raises(ValueError):
        ProviderPassMetadata(**_metadata_values(route_reason_codes=(reason_code,)))


def test_direct_metadata_constructor_rejects_document_like_control_values() -> None:
    for field, value in (
        ("pass_id", "document-key-123"),
        ("stage", "document text"),
        ("route_action", "AUTO_ROUTE"),
        ("route_reason_codes", ("DOCUMENT_CONTENT",)),
    ):
        with pytest.raises(ValueError):
            ProviderPassMetadata(**_metadata_values(**{field: value}))


def test_returned_model_id_is_optional_but_safe_when_present() -> None:
    metadata = ProviderPassMetadata(**_metadata_values(returned_model_id=None))

    assert metadata.as_safe_dict()["returned_model_id"] is None
    with pytest.raises(ValueError):
        ProviderPassMetadata(**_metadata_values(returned_model_id="model id with spaces"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt_sha256", "not-a-hash"),
        ("stage", "provider\u2603"),
        ("input_tokens", True),
        ("cached_input_tokens", 101),
        ("estimated_cost_usd", Decimal("NaN")),
    ],
)
def test_direct_metadata_constructor_validates_durable_field_boundaries(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        ProviderPassMetadata(**_metadata_values(**{field: value}))
