"""What an invoice has cost so far: its model calls (``invoice_model_call``) summed for the user (issue #500).

The ledger holds one row per request, reserved before it leaves and settled with the usage it consumed. A settled
row that reports ``usage_complete`` is provider-reported usage; a settled row without it, and a row that is still
reserved, stand in with their reservation, which is what the organization's monthly limit counts too. Nothing here
turns an unknown amount into a known one: usage that was not reported is reported as the reservation it is, and a
model without a price has no cost (a request at an unknown price reserves 0 USD, which is not a price).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Sequence

from pydantic import BaseModel

from app.models.invoice_model_call import InvoiceModelCall

_STAGES = {"read_page": "READING", "extract_invoice": "EXTRACTION"}
_STAGE_ORDER = {"READING": 0, "EXTRACTION": 1}
_COST_PLACES = Decimal("0.000001")


class InvoiceUsageOut(BaseModel):
    """Tokens and cost of a set of calls, with the vocabulary of the declaration import's run budget."""

    # REPORTED: every call reported its usage. PARTIAL: some did. RESERVED: none did, the reservation stands in.
    usage_status: Literal["REPORTED", "PARTIAL", "RESERVED"]
    reported_input_tokens: int | None
    reported_output_tokens: int | None
    # Conservative: reported usage where there is one, the reservation otherwise.
    input_tokens: int
    output_tokens: int
    # None when a model of these calls has no known price.
    estimated_cost_usd: Decimal | None


class InvoiceStageUsageOut(InvoiceUsageOut):
    stage: Literal["READING", "EXTRACTION"]
    model: str
    calls: int


class InvoiceModelUsageOut(BaseModel):
    stages: list[InvoiceStageUsageOut]
    total: InvoiceUsageOut | None


def _usage(calls: Sequence[InvoiceModelCall]) -> dict:
    reported = [call for call in calls if call.status == "SETTLED" and call.usage_complete]
    settled = [call for call in calls if call.status == "SETTLED"]

    def tokens(call: InvoiceModelCall) -> tuple[int, int, Decimal]:
        return (call.input_tokens, call.output_tokens, call.cost_usd) if call in settled else (
            call.reserved_input_tokens, call.reserved_output_tokens, call.reserved_cost_usd)

    amounts = [tokens(call) for call in calls]
    priced = all(call.reserved_cost_usd > 0 for call in calls)
    return {
        "usage_status": "REPORTED" if len(reported) == len(calls) else "PARTIAL" if reported else "RESERVED",
        "reported_input_tokens": sum(call.input_tokens for call in reported) if reported else None,
        "reported_output_tokens": sum(call.output_tokens for call in reported) if reported else None,
        "input_tokens": sum(amount[0] for amount in amounts),
        "output_tokens": sum(amount[1] for amount in amounts),
        "estimated_cost_usd": sum((amount[2] for amount in amounts), Decimal("0")).quantize(_COST_PLACES).normalize() if priced else None,
    }


def summarize(calls: Sequence[InvoiceModelCall]) -> InvoiceModelUsageOut:
    """The usage of every call of an invoice, over all its attempts, per stage and model and in total."""
    if not calls:
        return InvoiceModelUsageOut(stages=[], total=None)
    groups: dict[tuple[str, str], list[InvoiceModelCall]] = {}
    for call in calls:
        groups.setdefault((_STAGES[call.stage], call.model_name), []).append(call)
    stages = [InvoiceStageUsageOut(stage=stage, model=model, calls=len(group), **_usage(group))
              for (stage, model), group in sorted(groups.items(), key=lambda item: _STAGE_ORDER[item[0][0]])]
    return InvoiceModelUsageOut(stages=stages, total=InvoiceUsageOut(**_usage(calls)))
