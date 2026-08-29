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

    def _result(self, output):
        return ProviderCallResult(output=output.model_copy(deep=True), usage=deepcopy(self._usage))

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
        return self._result(self._inventory_output)

    async def extract_core(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[CoreExtractionOutput]:
        return self._result(self._core_output)

    async def extract_rules(
        self, documents: Sequence[DocumentInput], *, max_output_tokens: int
    ) -> ProviderCallResult[RuleExtractionOutput]:
        return self._result(self._rules_output)

    async def reconcile(
        self,
        inventory: DocumentInventoryOutput,
        core: CoreExtractionOutput,
        rules: RuleExtractionOutput,
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[ReconciliationOutput]:
        return self._result(self._reconciliation_output)

    async def recheck(
        self,
        documents: Sequence[DocumentInput],
        issue_codes: Sequence[str],
        subject_keys: Sequence[str],
        *,
        max_output_tokens: int,
    ) -> ProviderCallResult[TargetedRecheckOutput]:
        return self._result(self._recheck_output)
