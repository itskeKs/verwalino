"""A request that was sent and failed without reported usage is booked at its reservation, never at zero (issue #500).

Every transport is the real one (OpenAI SDK shape, OpenAI-compatible and Anthropic wire over a mock HTTP transport)
behind the real worker, reservation and ledger; only the network is faked. A request that provably never left is the
one case that costs nothing: one that failed while the name was resolved, in the endpoint guard, while connecting or in
the TLS handshake (issue #833), which the last tests run over real local sockets, and one raised before any attempt.
"""
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import httpx
import httpx2
import openai
import pytest
from pydantic import SecretStr

from app.core.db import get_worker_session_factory
from app.declarations.fake_provider import FakeDocumentReadingProvider, FakeInvoiceExtractionProvider
from app.declarations.invoice_pipeline import InvoiceProviders, _failed_amounts
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.pipeline import _Reservation
from app.declarations.provider import DocumentInput, PageInput, ProviderFailure, ProviderUsage
from app.declarations.provider_runtime import ProviderClient, create_extraction_provider
from app.declarations.schemas import InvoiceExtractionOutput, PageReadingOutput
from app.models.invoice_model_call import REQUEST_NOT_SENT
from app.services.ai_settings_service import EffectiveAiSettings
from app.workers.invoice_extraction import process_once
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data
from tests.local_provider_endpoints import UNRESOLVABLE, closed_port, listening, not_tls, provider_for, resolve_without_network
from tests.test_invoice_extraction_provider import ANSWER as INVOICE_ANSWER, _Encoder, _SdkResponse
from tests.test_invoice_model_extraction import (
    Harness,
    ai,
    leave_no_invoice_model_data as leave_no_invoice_model_data,
    ledger,
    monthly,
    no_proposal,
    process,
    scan_pdf,
    state,
    store as store,
    transcript,
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
        if step in {"reset", "refused"}:
            # What the SDK raises for a connection that broke while the request was out (its HTTP library's read error)
            # or could not be made at all (its connect error).
            error = openai.APIConnectionError(request=REQUEST)
            error.__cause__ = httpx2.ReadError("reset") if step == "reset" else httpx2.ConnectError("refused")
            return error
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
        if step == "reset":
            raise httpx.ReadError("reset", request=request)
        if step == "refused":
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


def configure(org, kind, monthly_limit_usd=None):
    configure_ai(org, import_enabled=True, monthly_limit_usd=monthly_limit_usd, **KINDS[kind])


@pytest.fixture(params=sorted(KINDS))
def kind(request):
    return request.param


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["reset", "timeout", "server"])
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
async def test_only_a_failure_that_never_left_books_zero():
    result = await FakeInvoiceExtractionProvider().extract_invoice(
        [DocumentInput("invoice", (PageInput("invoice", 1, "text", 1.0),))], categories=(), model="m", max_output_tokens=10)
    attempt = result.attempt_metadata
    reported = ProviderUsage(300, 20, Decimal("0.0001"), 1)
    cases = {
        "never left": (ProviderFailure("OPENAI_API_KEY_MISSING", False, "no client"), (0, 0, Decimal("0"), True)),
        "not sent: the connection could not be made": (ProviderFailure("PROVIDER_CONNECTION_ERROR", True, "refused", attempt_metadata=attempt, not_sent=True), (0, 0, Decimal("0"), True)),
        "not sent, with an attempt counted": (ProviderFailure("PROVIDER_CONNECTION_ERROR", True, "refused", attempt_metadata=attempt, attempt_count=1, not_sent=True), (0, 0, Decimal("0"), True)),
        "not sent, but usage reported": (ProviderFailure("PROVIDER_SCHEMA_FAILURE", False, "invalid", usage=reported, not_sent=True), (300, 20, Decimal("0.0001"), True)),
        "sent, the connection reset": (ProviderFailure("PROVIDER_CONNECTION_ERROR", True, "reset", attempt_metadata=attempt, not_sent=False), RESERVED),
        "usage reported": (ProviderFailure("PROVIDER_SCHEMA_FAILURE", False, "invalid", usage=reported), (300, 20, Decimal("0.0001"), True)),
        "sent, usage unknown": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", attempt_metadata=attempt), RESERVED),
        "sent, declared complete without usage": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", attempt_metadata=attempt, usage_complete=True), RESERVED),
        "an attempt on record, declared as none": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", attempt_metadata=attempt, usage_complete=True, attempt_count=0), RESERVED),
        "sent, complete, attempt counted": (ProviderFailure("PROVIDER_TIMEOUT", True, "slow", usage_complete=True, attempt_count=1), RESERVED),
        "usage that cannot be valid": (ProviderFailure("PROVIDER_SCHEMA_FAILURE", False, "invalid", usage=ProviderUsage(-1, 0, Decimal("0"), 1)), RESERVED),
    }
    assert {name: _failed_amounts(failure, RESERVATION) for name, (failure, _) in cases.items()} == {name: expected for name, (_, expected) in cases.items()}


# ---- a request that never left the host (issue #833) -------------------------------------------------------------

SITUATIONS = [(kind, situation) for kind in KINDS for situation in ("refused", "tls", "unresolvable", "guard") if (kind, situation) != ("openai", "guard")]


@asynccontextmanager
async def unreachable(situation, monkeypatch):
    """The base URL of an endpoint that cannot be connected to, for real, in one of the four ways a connection fails
    before the request is written: refused, a TLS handshake that fails, a name that does not resolve, and a name that
    resolves to an address the endpoint guard refuses (link-local here; a hosted deployment also refuses private ones)."""
    if situation == "refused":
        yield f"http://127.0.0.1:{closed_port()}/v1"
    elif situation == "tls":
        async with listening(not_tls) as port:
            yield f"https://127.0.0.1:{port}/v1"
    else:
        resolve_without_network(monkeypatch, address=None if situation == "unresolvable" else "169.254.169.254")
        yield f"https://{UNRESOLVABLE}/v1"


class Real:
    """The worker's provider factory with the real transport of one kind for both models, aimed at ``base_url``."""

    def __init__(self, kind, base_url):
        self.kind, self.base_url = kind, base_url

    def __call__(self, effective):
        return InvoiceProviders(reader=provider_for(self.kind, self.base_url, model=effective.document_model),
                                extractor=provider_for(self.kind, self.base_url, model=effective.model))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,situation", SITUATIONS)
async def test_a_request_that_could_not_be_sent_is_settled_as_not_sent_and_costs_nothing(worker_tenant, monkeypatch, kind, situation):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    configure(org, kind)
    original = scan_pdf()
    doc = await upload(api, original)
    async with unreachable(situation, monkeypatch) as base_url:
        assert await process_once(get_worker_session_factory(), org, providers=Real(kind, base_url))
    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("failed", "provider_unreachable")]
    assert no_proposal(document) and (await api.get(f"/api/invoice-documents/{doc['id']}/content")).content == original
    (row,) = await ledger(org)
    assert (row.status, row.usage_complete, row.error_code) == ("SETTLED", True, REQUEST_NOT_SENT)
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (0, 0, Decimal("0"))
    # What was reserved before the request stays on the row, so that a request that was not sent is told from one that was.
    assert row.reserved_input_tokens > 0 and row.reserved_output_tokens > 0
    assert (row.reserved_cost_usd > 0) == (kind != "openai_compatible"), "a model without a price reserves no money"
    assert await monthly(org) == 0
    assert await usage(api, doc) == {"stages": [], "total": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openai", "anthropic"])
async def test_three_attempts_that_could_not_be_sent_do_not_use_up_the_monthly_limit(worker_tenant, monkeypatch, kind):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    configure(org, kind)
    doc = await upload(api, scan_pdf())
    async with unreachable("refused", monkeypatch) as base_url:
        providers = Real(kind, base_url)
        assert await process_once(get_worker_session_factory(), org, providers=providers)
        (first,) = await ledger(org)
        reservation = first.reserved_cost_usd
        assert reservation > 0
        # A limit that one reservation of this size uses up on its own with the next: had the first attempt been
        # booked, the second would be refused before it was tried.
        configure(org, kind, monthly_limit_usd=reservation * Decimal("1.5"))
        for _ in range(2):
            assert (await api.post(f"/api/invoice-documents/{doc['id']}/retry")).status_code == 200
            assert await process_once(get_worker_session_factory(), org, providers=providers)
    _, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("failed", "provider_unreachable")] * 3
    rows = await ledger(org)
    assert [(row.status, row.error_code, row.reserved_cost_usd) for row in rows] == [("SETTLED", REQUEST_NOT_SENT, reservation)] * 3
    assert await monthly(org) == 0
    assert await usage(api, doc) == {"stages": [], "total": None}


@pytest.mark.asyncio
async def test_a_page_read_before_the_provider_became_unreachable_stays_booked_and_listed(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    ai(org)
    reader = FakeDocumentReadingProvider(
        transcripts=transcript(1, "Mustermann Versicherungen AG Rechnung RE-2026-0042"), usage=ProviderUsage(1000, 200, Decimal("0.00165"), 1),
        failures={("invoice", 2): ProviderFailure("PROVIDER_CONNECTION_ERROR", True, "refused", not_sent=True)})
    doc = await upload(api, scan_pdf(2))
    await process(org, Harness(reader=reader))
    assert [(a["status"], a["error_code"]) for a in (await state(api, doc))[1]] == [("failed", "provider_unreachable")]
    first, second = await ledger(org)
    assert (first.pdf_page, first.status, first.usage_complete, first.error_code) == (1, "SETTLED", True, None)
    assert (first.input_tokens, first.output_tokens, first.cost_usd) == (1000, 200, Decimal("0.00165"))
    assert (second.pdf_page, second.status, second.error_code) == (2, "SETTLED", REQUEST_NOT_SENT)
    assert (second.input_tokens, second.output_tokens, second.cost_usd) == (0, 0, Decimal("0"))
    # The month and the card hold what the page that was sent consumed, and no request for the one that was not.
    assert await monthly(org) == Decimal("0.00165")
    (stage,) = (await usage(api, doc))["stages"]
    assert (stage["calls"], stage["reported_input_tokens"], stage["estimated_cost_usd"]) == (1, 1000, "0.00165")


@pytest.mark.asyncio
async def test_a_repair_request_that_cannot_connect_after_a_reported_first_leg_stays_booked_at_its_reservation(worker_tenant, kind):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    configure(org, kind)
    # The first answer was received (the request was sent, its usage is reported); the connection of the one repair
    # request is refused. The logical request left the host, so it is not "not sent": what the first leg consumed is
    # part of the reservation, which stays booked.
    wire = Wire("malformed", "refused")
    doc = await upload(api, scan_pdf())
    assert await process_once(get_worker_session_factory(), org, providers=Transports(kind, wire))
    assert wire.requests == 2
    assert [(a["status"], a["error_code"]) for a in (await state(api, doc))[1]] == [("failed", "model_reading_failed")]
    (row,) = await ledger(org)
    assert (row.status, row.usage_complete, row.error_code) == ("SETTLED", False, "PROVIDER_CONNECTION_ERROR")
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (row.reserved_input_tokens, row.reserved_output_tokens, row.reserved_cost_usd)
    assert row.reserved_input_tokens >= MALFORMED_USAGE[0] and await monthly(org) == row.reserved_cost_usd


# ---- a model without a price that reports cache writes (issue #839) ----------------------------------------------------

def cache_writing_provider(model, parsed, tokens):
    """The real OpenAI provider over an SDK that answers once, reporting ``tokens`` of which a quarter were read from the
    prompt cache and half were written to it: what a newer model reports of itself."""
    cached, written = tokens[0] // 4, tokens[0] // 2

    async def parse(**kwargs):
        usage = SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1], total_tokens=sum(tokens),
                                input_tokens_details=SimpleNamespace(cached_tokens=cached, cache_write_tokens=written))
        return SimpleNamespace(output_parsed=parsed, usage=usage, status="completed", refusal=None, model=model,
                               _request_id="req_unpriced", output=[])

    async def close():
        return None

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse), close=close)
    return OpenAiDeclarationProvider(client=client, primary_model=model, escalation_model=model, token_encoder=_Encoder())


@pytest.mark.asyncio
async def test_two_unpriced_models_that_report_cache_writes_finish_the_invoice_booked_as_reported_at_no_cost(worker_tenant):
    api, ids = worker_tenant
    org = ids["org_a_id"]
    # The monthly limit is not enforced for a model without a price, and nothing of these calls counts towards it.
    ai(org, model="gpt-6.2-sol", document_model="gpt-6-luna", monthly_limit_usd=Decimal("0.01"))
    page = {"blocks": [{"kind": "TEXT", "text": "Mustermann Versicherungen AG Rechnung RE-2026-0042 Gesamtbetrag 312,00 EUR", "rows": None}]}
    harness = Harness(
        reader=cache_writing_provider("gpt-6-luna", PageReadingOutput.model_validate(page), (100, 5)),
        extractor=cache_writing_provider("gpt-6.2-sol", InvoiceExtractionOutput.model_validate(INVOICE_ANSWER), (1200, 90)))
    doc = await upload(api, scan_pdf())
    await process(org, harness)

    document, attempts = await state(api, doc)
    assert [(a["status"], a["error_code"]) for a in attempts] == [("succeeded", None)]
    assert document["processing_status"] == "succeeded" and document["extraction_payload"]["gross_amount"] == "312.00"
    # Each call is stored with the tokens it reported and a cost of zero, which is no price: the reservation of a request
    # at an unknown price is zero too.
    reading, extraction = await ledger(org)
    assert (reading.stage, reading.model_name, reading.status, reading.usage_complete, reading.error_code) == (
        "read_page", "gpt-6-luna", "SETTLED", True, None)
    assert (reading.input_tokens, reading.output_tokens, reading.cost_usd, reading.reserved_cost_usd) == (100, 5, Decimal("0"), Decimal("0"))
    assert (extraction.stage, extraction.model_name, extraction.status, extraction.usage_complete, extraction.error_code) == (
        "extract_invoice", "gpt-6.2-sol", "SETTLED", True, None)
    assert (extraction.input_tokens, extraction.output_tokens, extraction.cost_usd, extraction.reserved_cost_usd) == (
        1200, 90, Decimal("0"), Decimal("0"))
    assert await monthly(org) == 0
    # The usage card shows the reported tokens of both stages and no price at all, never 0 USD.
    shown = await usage(api, doc)
    assert [(stage["stage"], stage["model"], stage["usage_status"], stage["reported_input_tokens"], stage["reported_output_tokens"],
             stage["estimated_cost_usd"]) for stage in shown["stages"]] == [
        ("READING", "gpt-6-luna", "REPORTED", 100, 5, None), ("EXTRACTION", "gpt-6.2-sol", "REPORTED", 1200, 90, None)]
    assert (shown["total"]["usage_status"], shown["total"]["input_tokens"], shown["total"]["output_tokens"],
            shown["total"]["estimated_cost_usd"]) == ("REPORTED", 1300, 95, None)
