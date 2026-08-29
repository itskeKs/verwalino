"""Strict, content-free metadata for declaration provider pass observability."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any, ClassVar, Mapping


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CLASS = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_MAX_IDENTIFIER_LENGTH = 128
_MAX_REQUEST_ID_LENGTH = 256
_MAX_TOKEN_COUNT = 1_000_000_000
_FORBIDDEN_FIELDS = frozenset(
    {
        "quote",
        "prompt",
        "document_key",
        "image_data_url",
        "response_body",
        "tenant_id",
        "path",
        "exception_message",
    }
)
_PASS_IDS = frozenset({"inventory", "extract_core", "extract_rules", "reconcile", "recheck"})
_STAGES = frozenset({"provider", "schema_repair"})
_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_ROUTE_ACTIONS = frozenset({"NONE", "VISION_RECHECK", "HUMAN_REVIEW"})
_ROUTE_REASON_CODES = frozenset(
    {
        "PROVIDER_REFUSAL",
        "PROVIDER_INCOMPLETE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_SCHEMA_FAILURE",
        "UNKNOWN_EVIDENCE_DOCUMENT",
        "EVIDENCE_PAGE_OUT_OF_RANGE",
        "EVIDENCE_QUOTE_NOT_FOUND",
        "LOW_PAGE_QUALITY",
        "DOMAIN_BLOCKER",
    }
)


def _validate_identifier(value: object, field_name: str, *, maximum: int = _MAX_IDENTIFIER_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not value.isascii()
        or _SAFE_IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(f"invalid safe {field_name}")
    return value


def _validate_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid {field_name}")
    return value


def _validate_nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_TOKEN_COUNT:
        raise ValueError(f"invalid {field_name}")
    return value


@dataclass(frozen=True)
class StaticContractFingerprints:
    """Hashes of only the static developer prompt, schema, and harness."""

    prompt_sha256: str
    schema_sha256: str
    harness_sha256: str


def fingerprint_static_contract(
    *, developer_prompt: str, schema: Mapping[str, Any], harness: str
) -> StaticContractFingerprints:
    """Fingerprint independent static artifacts without accepting request data."""
    if not isinstance(developer_prompt, str) or not isinstance(harness, str):
        raise ValueError("static contract artifacts must be text")
    if not isinstance(schema, Mapping):
        raise ValueError("static contract schema must be a mapping")
    try:
        canonical_schema = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError("static contract schema must be canonical JSON") from error
    return StaticContractFingerprints(
        prompt_sha256=sha256(developer_prompt.encode("utf-8")).hexdigest(),
        schema_sha256=sha256(canonical_schema.encode("utf-8")).hexdigest(),
        harness_sha256=sha256(harness.encode("utf-8")).hexdigest(),
    )


@dataclass(frozen=True)
class ProviderPassMetadata:
    """Validated allowlist of values safe for a durable provider-pass ledger."""

    pass_id: str
    attempt_no: int
    stage: str
    configured_model: str
    returned_model_id: str | None
    reasoning_effort: str
    request_id: str | None
    prompt_version: str
    prompt_sha256: str
    schema_version: str
    schema_sha256: str
    harness_version: str
    harness_sha256: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    latency_ms: int
    retry_count: int
    route_action: str
    route_reason_codes: tuple[str, ...]
    failure_class: str | None

    _ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "pass_id", "attempt_no", "stage", "configured_model", "returned_model_id",
            "reasoning_effort", "request_id", "prompt_version", "prompt_sha256",
            "schema_version", "schema_sha256", "harness_version", "harness_sha256",
            "input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
            "estimated_cost_usd", "latency_ms", "retry_count", "route_action",
            "route_reason_codes", "failure_class",
        }
    )

    @classmethod
    def from_values(cls, **values: object) -> "ProviderPassMetadata":
        unknown = set(values) - cls._ALLOWED_FIELDS
        if unknown & _FORBIDDEN_FIELDS:
            # These names commonly carry content or tenant context.  The
            # generic error deliberately retains neither the key nor its value.
            raise ValueError("unsafe provider pass metadata field")
        if unknown:
            # Intentionally do not echo keys or values; callers may have passed content.
            raise ValueError("unsafe provider pass metadata field")
        if set(values) != cls._ALLOWED_FIELDS:
            raise ValueError("incomplete provider pass metadata")
        return cls(**values)  # type: ignore[arg-type]

    def __post_init__(self) -> None:
        for field_name in (
            "configured_model", "prompt_version", "schema_version", "harness_version",
        ):
            _validate_identifier(getattr(self, field_name), field_name)
        if self.pass_id not in _PASS_IDS:
            raise ValueError("invalid pass_id")
        if self.stage not in _STAGES:
            raise ValueError("invalid stage")
        if self.reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError("invalid reasoning_effort")
        if self.route_action not in _ROUTE_ACTIONS:
            raise ValueError("invalid route_action")
        if self.returned_model_id is not None:
            _validate_identifier(self.returned_model_id, "returned_model_id")
        if self.request_id is not None:
            _validate_identifier(self.request_id, "request_id", maximum=_MAX_REQUEST_ID_LENGTH)
        for field_name in ("prompt_sha256", "schema_sha256", "harness_sha256"):
            _validate_sha256(getattr(self, field_name), field_name)
        for field_name in (
            "attempt_no", "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "latency_ms", "retry_count",
        ):
            _validate_nonnegative_int(getattr(self, field_name), field_name)
        if self.attempt_no < 1:
            raise ValueError("invalid attempt_no")
        if self.cached_input_tokens + self.cache_write_input_tokens > self.input_tokens:
            raise ValueError("invalid cache token partition")
        if not isinstance(self.estimated_cost_usd, Decimal) or not self.estimated_cost_usd.is_finite() or self.estimated_cost_usd < 0:
            raise ValueError("invalid estimated_cost_usd")
        if not isinstance(self.route_reason_codes, tuple):
            raise ValueError("invalid route_reason_codes")
        for code in self.route_reason_codes:
            if code not in _ROUTE_REASON_CODES:
                raise ValueError("invalid route_reason_code")
        if self.failure_class is not None:
            if not isinstance(self.failure_class, str) or _FAILURE_CLASS.fullmatch(self.failure_class) is None:
                raise ValueError("invalid failure_class")

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "pass_id": self.pass_id,
            "attempt_no": self.attempt_no,
            "stage": self.stage,
            "configured_model": self.configured_model,
            "returned_model_id": self.returned_model_id,
            "reasoning_effort": self.reasoning_effort,
            "request_id": self.request_id,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "schema_version": self.schema_version,
            "schema_sha256": self.schema_sha256,
            "harness_version": self.harness_version,
            "harness_sha256": self.harness_sha256,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": str(self.estimated_cost_usd),
            "latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "route_action": self.route_action,
            "route_reason_codes": list(self.route_reason_codes),
            "failure_class": self.failure_class,
        }


@dataclass(frozen=True)
class PassLedgerEntry(ProviderPassMetadata):
    """A durable representation with no fields beyond provider pass metadata."""

    @classmethod
    def from_metadata(cls, metadata: ProviderPassMetadata) -> "PassLedgerEntry":
        if not isinstance(metadata, ProviderPassMetadata):
            raise ValueError("invalid provider pass metadata")
        return cls(**metadata.as_safe_dict() | {"route_reason_codes": metadata.route_reason_codes, "estimated_cost_usd": metadata.estimated_cost_usd})
