"""A request that was sent and failed without reported usage is booked at its reservation, never at zero (issue #500).

Every transport is the real one (OpenAI SDK shape, OpenAI-compatible and Anthropic wire over a mock HTTP transport)
behind the real worker, reservation and ledger; only the network is faked. A request that provably never left is the
one case that costs nothing.
"""
from decimal import Decimal
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import SecretStr

from app.core.db import get_worker_session_factory
from app.declarations.fake_provider import FakeInvoiceExtractionProvider
from app.declarations.invoice_pipeline import InvoiceProviders, _failed_amounts
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.pipeline import _Reservation
from app.declarations.provider import DocumentInput, PageInput, ProviderFailure, ProviderUsage
from app.declarations.provider_runtime import ProviderClient, create_extraction_provider
from app.services.ai_settings_service import EffectiveAiSettings
from app.workers.invoice_extraction import process_once
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.test_invoice_extraction_provider import _Encoder, _SdkResponse
from tests.test_invoice_model_extraction import (
    leave_no_invoice_model_data as leave_no_invoice_model_data,
    ledger,
    monthly,
    no_proposal,
    scan_pdf,
    state,
    store as store,
    tesseract_unusable as tesseract_unusable,
    upload,
    usage,
    worker_tenant as worker_tenant,
)
from tests.ai_settings_support import configure_ai

KINDS = {
    "openai": dict(provider="openai", model="gpt-5.6-luna", document_model="gpt-5.4-mini", base_url=None),
    "openai_compatible": dict(provider="openai_compatible", model="local-text", document_model="local-vision", base_url="http://localhost:11434/v1"),
    "anthropic": dict(provider="anthropic", model="claude-haiku-4-5", document_model="claude-haiku-4-5", base_url=None),
}
REQUEST = httpx.Request("POST", "https://provider.invalid/v1/responses")
MALFORMED_USAGE = (2000, 100)


class Wire:
    """What the provider's side of the connection does, request after request; the last step repeats."""

    def __init__(self, *steps):
        self.steps, self.requests = list(steps), 0

    def next(self):
        self.requests += 1
        return self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]


def openai_transport(model, wire):
    def outcome(step):
        if step == "connection":
            return openai.APIConnectionError(request=REQUEST)
        if step == "timeout":
            return openai.APITimeoutError(request=REQUEST)
        if step == "server":
            return openai.InternalServerError("server", response=httpx.Response(500, request=REQUEST), body=None)
        return _SdkResponse(None, usage=MALFORMED_USAGE)

    async def parse(**kwargs):
        step = outcome(wire.next())
        if isinstance(step, BaseException):
            raise step
        return step

    async def close():
        return None

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse), close=close)
    return OpenAiDeclarationProvider(client=client, primary_model=model, escalation_model=model, token_encoder=_Encoder())


def http_transport(kind, model, wire, base_url):
    provider = create_extraction_provider(EffectiveAiSettings(provider=kind, api_key=SecretStr("synthetic-key"), model=model, base_url=base_url))

    def handle(request):
        step = wire.next()
        if step == "connection":
            raise httpx.ConnectError("refused", request=request)
        if step == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if step == "server":
            return httpx.Response(500, json={})
        text = '{"blocks": 1}'
        if kind == "anthropic":
            return httpx.Response(200, json={"id": "msg", "model": model, "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
                                             "usage": {"input_tokens": MALFORMED_USAGE[0], "output_tokens": MALFORMED_USAGE[1]}})
        return httpx.Response(200, json={"id": "chat", "model": model, "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                                         "usage": {"prompt_tokens": MALFORMED_USAGE[0], "completion_tokens": MALFORMED_USAGE[1]}})

    provider._client = ProviderClient(provider=kind, api_key="synthetic-key", base_url=provider._client.base_url, transport=httpx.MockTransport(handle))
    return provider


class Transports:
    """The worker's provider factory for one kind of transport: a reading and an extraction provider over their own wire."""

    def __init__(self, kind, reading_wire, extraction_wire=None, *, never_sends=False):
        self.kind, self.reading, self.extraction, self.never_sends = kind, reading_wire, extraction_wire or Wire("server"), never_sends

    def __call__(self, effective):
        spec = KINDS[self.kind]
        build = (lambda model, wire: openai_transport(model, wire)) if self.kind == "openai" else (
            lambda model, wire: http_transport(self.kind, model, wire, spec["base_url"]))
        reader, extractor = build(effective.document_model, self.reading), build(effective.model, self.extraction)
        if self.never_sends:
            # The request cannot even be sent: no client, and the failure is raised before anything leaves.
            reader._client = None
        return InvoiceProviders(reader=reader, extractor=extractor)


def configure(org, kind):
    configure_ai(org, import_enabled=True, monthly_limit_usd=None, **KINDS[kind])


@pytest.fixture(params=sorted(KINDS))
def kind(request):
    return request.param


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["connection", "timeout", "server"])
async def test_a_request_that_was_sent_and_failed_without_usage_stays_booked_at_its_reservation(worker_tenant, kind, step):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    configure(org, kind)
    wire = Wire(step)
    original = scan_pdf()
    doc = await upload(api, original)
    assert await process_once(get_worker_session_factory(), org, providers=Transports(kind, wire))
    assert wire.requests >= 1
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("failed", "model_reading_failed")]
    assert no_proposal(document) and (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    (row,) = await ledger(org)
    assert (row.status, row.usage_complete) == ("SETTLED", False)
    assert row.reserved_input_tokens > 0 and (row.input_tokens, row.output_tokens, row.cost_usd) == (
        row.reserved_input_tokens, row.reserved_output_tokens, row.reserved_cost_usd)
    assert await monthly(org) == row.reserved_cost_usd
    (shown,) = (await usage(api, doc))["stages"]
    assert (shown["usage_status"], shown["input_tokens"]) == ("RESERVED", row.reserved_input_tokens)


@pytest.mark.asyncio
async def test_a_repair_request_that_fails_after_a_reported_first_leg_stays_booked_at_its_reservation(worker_tenant, kind):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    configure(org, kind)
    # The first answer is not the schema (its usage is reported); the one repair request is lost: what the second
    # consumed is unknown, so the whole logical request stays at its reservation, which covers both.
    wire = Wire("malformed", "server")
    doc = await upload(api, scan_pdf())
    assert await process_once(get_worker_session_factory(), org, providers=Transports(kind, wire))
    assert wire.requests == 2
    assert [(a["status"], a["error_code"]) for a in (await state(api, doc))[1]] == [("failed", "model_reading_failed")]
    (row,) = await ledger(org)
    assert (row.status, row.usage_complete) == ("SETTLED", False)
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (row.reserved_input_tokens, row.reserved_output_tokens, row.reserved_cost_usd)
    assert row.reserved_input_tokens >= MALFORMED_USAGE[0] and await monthly(org) == row.reserved_cost_usd


@pytest.mark.asyncio
async def test_a_request_that_provably_never_left_costs_nothing(worker_tenant, kind):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    configure(org, kind)
    wire = Wire("server")
    doc = await upload(api, scan_pdf())
    assert await process_once(get_worker_session_factory(), org, providers=Transports(kind, wire, never_sends=True))
    assert wire.requests == 0
    assert [(a["status"], a["error_code"]) for a in (await state(api, doc))[1]] == [("failed", "model_reading_failed")]
    (row,) = await ledger(org)
    assert (row.status, row.usage_complete, row.error_code) == ("SETTLED", True, "OPENAI_API_KEY_MISSING")
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (0, 0, Decimal("0"))
    assert await monthly(org) == 0


RESERVATION = _Reservation(input_tokens=5000, output_tokens=6000, cost_usd=Decimal("0.012"))
RESERVED = (5000, 6000, Decimal("0.012"), False)


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_only_a_failure_without_any_attempt_usage_or_metadata_books_zero():
    result = await FakeInvoiceExtractionProvider().extract_invoice(
        [DocumentInput("invoice", (PageInput("invoice", 1, "text", 1.0),))], categories=(), model="m", max_output_tokens=10)
    attempt = result.attempt_metadata
    reported = ProviderUsage(300, 20, Decimal("0.0001"), 1)
    cases = {
        "never left": (ProviderFailure("OPENAI_API_KEY_MISSING", False, "no client"), (0, 0, Decimal("0"), True)),
        "usage reported": (ProviderFailure("PROVIDER_SCHEMA_FAILURE", False, "invalid", usage=reported), (300, 20, Decimal("0.0001"), True)),
        "sent, usage unknown": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", attempt_metadata=attempt), RESERVED),
        "sent, declared complete without usage": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", attempt_metadata=attempt, usage_complete=True), RESERVED),
        "an attempt on record, declared as none": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", attempt_metadata=attempt, usage_complete=True, attempt_count=0), RESERVED),
        "sent, complete, attempt counted": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", usage_complete=True, attempt_count=1), RESERVED),
        "usage that cannot be valid": (ProviderFailure("PROVIDER_SCHEMA_FAILURE", False, "invalid", usage=ProviderUsage(-1, 0, Decimal("0"), 1)), RESERVED),
    }
    assert {name: _failed_amounts(failure, RESERVATION) for name, (failure, _) in cases.items()} == {name: expected for name, (_, expected) in cases.items()}
