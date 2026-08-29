"""Deterministic provider for tests and local, no-network flows."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from decimal import Decimal

from app.declarations.provider import (
    DocumentInput,
    ProviderCallResult,
    ProviderReservationEstimate,
    ProviderUsage,
)
from app.declarations.observability import ProviderPassMetadata
from app.declarations.schemas import (
    CoreExtractionOutput,
    DocumentInventoryOutput,
    ReconciliationOutput,
    RuleExtractionOutput,
    TargetedRecheckOutput,
)


class FakeDeclarationProvider:
    """Strict outputs with isolated copies on every successful call."""

    def __init__(
        self,
        inventory_output: DocumentInventoryOutput,
        core_output: CoreExtractionOutput,
        rules_output: RuleExtractionOutput,
        reconciliation_output: ReconciliationOutput,
        recheck_output: TargetedRecheckOutput,
        usage: ProviderUsage | None = None,
    ) -> None:
        self._inventory_output = inventory_output
        self._core_output = core_output
        self._rules_output = rules_output
        self._reconciliation_output = reconciliation_output
        self._recheck_output = recheck_output
        self._usage = usage or ProviderUsage(
            input_tokens=17,
            output_tokens=11,
            cost_usd=Decimal("0.000001"),
            attempt_count=1,
        )

    def _result(self, pass_id: str, output):
        usage = deepcopy(self._usage)
        if pass_id == "recheck":
            configured_model = "gpt-5.4-mini"
            reasoning_effort = "none"
        else:
            configured_model = "gpt-5.6-luna"
            reasoning_effort = "medium"
        metadata = ProviderPassMetadata.from_values(
            pass_id=pass_id,
            attempt_no=1,
            stage="provider",
            configured_model=configured_model,
            returned_model_id=configured_model,
            reasoning_effort=reasoning_effort,
            request_id=None,
            prompt_version="fake-v1",
            prompt_sha256="0" * 64,
            schema_version="1",
            schema_sha256="1" * 64,
            harness_version="1",
            harness_sha256="2" * 64,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.cost_usd,
            latency_ms=0,
            retry_count=max(usage.attempt_count - 1, 0),
            route_action="NONE",
            route_reason_codes=(),
            failure_class=None,
        )
        return ProviderCallResult(
            output=output.model_copy(deep=True),
            usage=usage,
            metadata=metadata,
            attempt_metadata=(metadata,),
        )

    def estimate_reservation(
        self, method: str, *, max_output_tokens: int, **request: object
    ) -> ProviderReservationEstimate:
        """The in-process fake has no hidden prompt or retry request."""
        return ProviderReservationEstimate(
            input_tokens=self._usage.input_tokens,
            attempt_count=1,
            attempt_input_tokens=(self._usage.input_tokens,),
        )

    async def inventory(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[DocumentInventoryOutput]:
        return self._result("inventory", self._inventory_output)

    async def extract_core(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[CoreExtractionOutput]:
        return self._result("extract_core", self._core_output)

    async def extract_rules(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[RuleExtractionOutput]:
        return self._result("extract_rules", self._rules_output)

    async def reconcile(
        self,
        inventory: DocumentInventoryOutput,
        core: CoreExtractionOutput,
        rules: RuleExtractionOutput,
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[ReconciliationOutput]:
        return self._result("reconcile", self._reconciliation_output)

    async def recheck(
        self,
        documents: Sequence[DocumentInput],
        issue_codes: Sequence[str],
        subject_keys: Sequence[str],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[TargetedRecheckOutput]:
        return self._result("recheck", self._recheck_output)
