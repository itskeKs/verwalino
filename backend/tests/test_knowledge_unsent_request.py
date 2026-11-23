"""A property question whose provider connection never opened costs nothing; one that was sent keeps its reservation (issue #835).

The same fact the invoice path and the declaration import read (``ProviderFailure.not_sent``, issue #833) is carried by
the knowledge providers on ``ProviderUnavailable.not_sent``: the name did not resolve, the endpoint guard refused the
address, or the connection could not be made, so no byte of the request was written. Everything from the moment a
request byte may have been written (a reset, a protocol error, a timeout) is unknown and stays False: unknown is never
zero. The three transports are the real ones (the OpenAI SDK, the OpenAI-compatible and the Anthropic wire) over real
local sockets; only the far end is a local stand-in, no name is looked up on a network and no key is real. The
service tests run the route, the reservation and the ledger for real.
"""
import asyncio
from decimal import ROUND_CEILING, Decimal
from types import SimpleNamespace

import httpx
import httpx2
import pytest
import pytest_asyncio
from openai import APIConnectionError, AsyncOpenAI, RateLimitError
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.api.property_knowledge import get_knowledge_provider
from app.core.ai_models import model_price, token_cost
from app.core.config import settings
from app.core.db import get_tenant_session
from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
from app.declarations.knowledge_provider import (
    KnowledgeProviderRequest,
    ProviderInvalidOutput,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.declarations.openai_knowledge_provider import OpenAIKnowledgeProvider
from app.declarations.provider import ProviderUsage
from app.declarations.provider_runtime import create_knowledge_provider
from app.main import app
from app.models.property_knowledge_query_run import PropertyKnowledgeQueryRun
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.services.ai_settings_service import EffectiveAiSettings
from tests.local_provider_endpoints import (
    KINDS,
    MODELS,
    UNRESOLVABLE,
    close_after_request,
    closed_port,
    listening,
    never_answer,
    not_tls,
    resolve_without_network,
)

QUESTION = KnowledgeProviderRequest(
    normalized_question="Wie werden Heizkosten verteilt?",
    hits=[],
    allowed_fact_ids=[],
    allowed_rule_ids=[],
    allowed_source_ids=[],
    schema_version="knowledge-answer-v1",
    prompt_version="knowledge-qa-v1",
)
PRICED_KINDS = ("openai", "anthropic")


def knowledge_provider_for(kind: str, base_url: str, *, timeout: int | None = None):
    """The real knowledge transport of ``kind`` aimed at ``base_url``; the OpenAI one over a real SDK client."""
    if kind == "openai":
        client = AsyncOpenAI(api_key="synthetic-key", base_url=base_url, max_retries=0)
        return OpenAIKnowledgeProvider(client=client, model=MODELS[kind], timeout_seconds=timeout)
    return create_knowledge_provider(EffectiveAiSettings(
        provider=kind, api_key=SecretStr("synthetic-key"), model=MODELS[kind], qa_enabled=True,
        base_url=base_url, request_timeout_seconds=timeout))


async def failure_of(kind: str, base_url: str, *, timeout: int | None = None) -> Exception:
    provider = knowledge_provider_for(kind, base_url, timeout=timeout)
    try:
        with pytest.raises((ProviderUnavailable, ProviderTimeout, ProviderInvalidOutput)) as raised:
            await provider.answer(QUESTION, max_output_tokens=500)
        return raised.value
    finally:
        await provider._client.close()


@pytest.fixture(params=KINDS)
def kind(request):
    return request.param


# ---- before the request was written: not sent --------------------------------------------------------------

@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_refused_connection_is_not_sent(kind):
    failure = await failure_of(kind, f"http://127.0.0.1:{closed_port()}/v1")
    assert (type(failure), failure.not_sent) == (ProviderUnavailable, True)


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_failed_tls_handshake_is_not_sent(kind):
    async with listening(not_tls) as port:
        failure = await failure_of(kind, f"https://127.0.0.1:{port}/v1")
    assert (type(failure), failure.not_sent) == (ProviderUnavailable, True)


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_name_that_does_not_resolve_is_not_sent(kind, monkeypatch):
    resolve_without_network(monkeypatch, address=None)
    failure = await failure_of(kind, f"https://{UNRESOLVABLE}/v1")
    assert (type(failure), failure.not_sent) == (ProviderUnavailable, True)


@pytest.mark.no_db
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openai_compatible", "anthropic"])
async def test_an_address_the_endpoint_guard_refuses_is_not_sent(kind, monkeypatch):
    # The OpenAI SDK has no guard of ours: its endpoint is fixed. The two wire transports resolve the name themselves,
    # and a hosted deployment refuses an address that is not public before anything is connected: the name resolves to
    # a loopback address where a server is listening, and that server is never reached.
    connections = []

    async def listener(reader, writer):
        connections.append(writer)
        writer.close()

    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    resolve_without_network(monkeypatch, address="127.0.0.1")
    async with listening(listener) as port:
        failure = await failure_of(kind, f"https://{UNRESOLVABLE}:{port}/v1")
        await asyncio.sleep(0.05)
    assert (type(failure), failure.not_sent) == (ProviderUnavailable, True)
    assert connections == []


# ---- from the moment the request was written: unknown --------------------------------------------------------

@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_connection_closed_on_a_request_that_was_read_is_unknown(kind):
    async with listening(close_after_request) as port:
        failure = await failure_of(kind, f"http://127.0.0.1:{port}/v1")
    assert (type(failure), failure.not_sent) == (ProviderUnavailable, False)


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_no_answer_in_time_is_a_timeout_and_never_not_sent(kind):
    async with listening(never_answer) as port:
        failure = await failure_of(kind, f"http://127.0.0.1:{port}/v1", timeout=1)
    assert type(failure) is ProviderTimeout
    assert not getattr(failure, "not_sent", False)


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_timeout_while_connecting_stays_unknown(kind, monkeypatch):
    # A timeout is never proof, wherever it happened. The SDK speaks through httpx2, the wire transports through httpx.
    def stalled(library):
        async def send(self, *args, **options):
            raise library.ConnectTimeout("stalled")
        return send

    monkeypatch.setattr(httpx.AsyncClient, "send", stalled(httpx))
    monkeypatch.setattr(httpx2.AsyncClient, "send", stalled(httpx2))
    failure = await failure_of(kind, f"http://127.0.0.1:{closed_port()}/v1")
    assert type(failure) is ProviderTimeout
    assert not getattr(failure, "not_sent", False)


def sdk_failing_with(error):
    """An OpenAI-SDK-shaped client whose request fails the way the SDK reports it."""
    async def parse(**options):
        raise error

    return OpenAIKnowledgeProvider(client=SimpleNamespace(responses=SimpleNamespace(parse=parse)), model=MODELS["openai"])


def connection_error(cause):
    error = APIConnectionError(request=httpx2.Request("POST", "https://unit.test/responses"))
    error.__cause__ = cause
    return error


@pytest.mark.no_db
@pytest.mark.asyncio
@pytest.mark.parametrize("cause, not_sent", [
    (httpx2.ConnectError("refused"), True),
    (httpx.ConnectError("refused"), True),
    # A connection error that names no phase, or a phase after the connection was made, is unknown.
    (None, False),
    (httpx2.ReadError("reset"), False),
    (httpx2.WriteError("broken pipe"), False),
    (httpx2.RemoteProtocolError("closed"), False),
    (OSError("anything"), False),
], ids=["httpx2 connect error", "httpx connect error", "no cause", "read error", "write error", "protocol error", "other error"])
async def test_the_phase_of_the_sdk_connection_error_decides(cause, not_sent):
    provider = sdk_failing_with(connection_error(cause))
    with pytest.raises(ProviderUnavailable) as raised:
        await provider.answer(QUESTION, max_output_tokens=500)
    assert raised.value.not_sent is not_sent


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_a_failure_that_is_not_a_connection_error_is_never_not_sent():
    # A rate limit is an answer; an unexpected exception says nothing about the phase.
    limited = RateLimitError("limited", response=httpx2.Response(429, request=httpx2.Request("POST", "https://unit.test")), body=None)
    for error in (limited, RuntimeError("boom")):
        with pytest.raises(ProviderUnavailable) as raised:
            await sdk_failing_with(error).answer(QUESTION, max_output_tokens=500)
        assert raised.value.not_sent is False


@pytest.mark.no_db
def test_reported_usage_excludes_not_sent():
    usage = ProviderUsage(17, 11, Decimal("0.000017"), 1)
    assert ProviderUnavailable(not_sent=True).not_sent is True
    assert ProviderUnavailable().not_sent is False
    assert ProviderUnavailable(not_sent=True, usage=usage).not_sent is False


# ---- the service: the ledger row and the monthly total ----------------------------------------------------------

# One reservation must fit the monthly limit that is set and two never may: the limit has the granularity of a cent, so
# a reservation is sized to about 1.65 cents and the limit is the usage so far rounded up to the cent, plus two cents.
TARGET_RESERVATION = Decimal("0.0165")


@pytest_asyncio.fixture
async def ledger_cleanup(approved_knowledge_graph, migrator_session):
    yield
    await migrator_session.execute(delete(PropertyKnowledgeQueryRun).where(
        PropertyKnowledgeQueryRun.property_id == approved_knowledge_graph.property_id))


@pytest.fixture
def overrides():
    yield app.dependency_overrides
    app.dependency_overrides.pop(get_knowledge_provider, None)


async def monthly_usage(graph, actor) -> Decimal:
    async with get_tenant_session(graph.organization_id) as db:
        await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(actor)})
        return await OrganizationAiSettingsRepository(db, graph.organization_id).monthly_usage()


async def ledger_rows(graph) -> list[PropertyKnowledgeQueryRun]:
    async with get_tenant_session(graph.organization_id) as db:
        rows = await db.scalars(select(PropertyKnowledgeQueryRun).where(
            PropertyKnowledgeQueryRun.property_id == graph.property_id).order_by(PropertyKnowledgeQueryRun.created_at))
        return list(rows)


async def sized_for_the_limit(kind: str, monkeypatch) -> int:
    """The output allowance that makes one question's reservation about ``TARGET_RESERVATION`` on ``kind``'s model."""
    provider_name = "anthropic" if kind == "anthropic" else "openai"
    probe = knowledge_provider_for(kind, f"http://127.0.0.1:{closed_port()}/v1")
    try:
        input_tokens = probe.estimate_reservation(QUESTION, max_output_tokens=1).input_tokens
    finally:
        await probe._client.close()
    price = model_price(MODELS[kind], provider_name)
    output_tokens = int((TARGET_RESERVATION - token_cost(MODELS[kind], input_tokens, 0, provider_name)) * 1_000_000 / price.output)
    monkeypatch.setattr(settings, "declaration_qa_max_output_tokens", output_tokens)
    monkeypatch.setattr(settings, "declaration_qa_max_cost_usd", Decimal("100"))
    return output_tokens


class Question:
    """One organization's property question against one provider address, asked through the route."""

    def __init__(self, graph, client, seed_data, configured_ai):
        self.graph, self.client, self.route = graph, client, f"/api/properties/{graph.property_id}/knowledge/query"
        self.actor, self.configured_ai = seed_data["user_a_admin_id"], configured_ai

    async def limit_to_two_cents_above_the_usage(self) -> Decimal:
        prior = await monthly_usage(self.graph, self.actor)
        self.configured_ai(self.graph.organization_id, monthly_limit_usd=prior.quantize(Decimal("0.01"), rounding=ROUND_CEILING) + Decimal("0.02"))
        return prior

    async def ask(self, provider):
        app.dependency_overrides[get_knowledge_provider] = lambda: provider
        return await self.client.post(self.route, json={"question": "Heizkosten"})


@pytest.fixture
def question(approved_knowledge_graph, client_a_admin, seed_data, configured_ai, overrides, ledger_cleanup):
    return Question(approved_knowledge_graph, client_a_admin, seed_data, configured_ai)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PRICED_KINDS)
async def test_repeated_questions_at_an_address_nobody_listens_on_do_not_use_up_the_monthly_limit(kind, question, monkeypatch):
    await sized_for_the_limit(kind, monkeypatch)
    prior = await question.limit_to_two_cents_above_the_usage()
    answers = []
    for _ in range(3):
        provider = knowledge_provider_for(kind, f"http://127.0.0.1:{closed_port()}/v1")
        answers.append(await question.ask(provider))
    # The money first: nothing is booked, so the monthly total is where it was and the limit still has its room.
    assert await monthly_usage(question.graph, question.actor) == prior
    rows = await ledger_rows(question.graph)
    assert [row.estimated_cost_usd for row in rows] == [Decimal("0")] * len(rows)
    for row in rows:
        # The row still records the failed question with its error; only the consumed amount is zero.
        assert (row.status, row.input_tokens, row.output_tokens) == ("FINALIZED", 0, 0)
        assert (row.outcome, row.error_code) == ("PROVIDER_UNAVAILABLE", "PROVIDER_UNAVAILABLE")
        assert (row.initiated_by, row.latency_ms is not None, row.finalized_at is not None) == (question.actor, True, True)
        assert Decimal("0.015") < row.reserved_cost_usd <= Decimal("0.02")
        assert row.provider == type(provider).__name__ and row.model == MODELS[kind]
    # Every question reached the provider (none was refused for the limit) and says the provider could not be reached.
    assert [(answer.status_code, answer.json()["detail"]) for answer in answers] == [(503, "QA_TEMPORARILY_UNAVAILABLE")] * 3
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_repeated_questions_at_an_unpriced_compatible_endpoint_are_answered_as_unreachable_at_zero(question):
    for _ in range(3):
        answer = await question.ask(knowledge_provider_for("openai_compatible", f"http://127.0.0.1:{closed_port()}/v1"))
        assert (answer.status_code, answer.json()["detail"]) == (503, "QA_TEMPORARILY_UNAVAILABLE"), answer.text
    rows = await ledger_rows(question.graph)
    assert [(row.status, row.estimated_cost_usd, row.outcome) for row in rows] == [("FINALIZED", Decimal("0"), "PROVIDER_UNAVAILABLE")] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", PRICED_KINDS)
@pytest.mark.parametrize("handler, timeout", [(close_after_request, None), (never_answer, 1)], ids=["reset after the request", "no answer in time"])
async def test_a_question_that_was_sent_and_failed_keeps_its_reservation(kind, handler, timeout, question, monkeypatch):
    await sized_for_the_limit(kind, monkeypatch)
    prior = await question.limit_to_two_cents_above_the_usage()
    async with listening(handler) as port:
        first = await question.ask(knowledge_provider_for(kind, f"http://127.0.0.1:{port}/v1", timeout=timeout))
    assert first.status_code in {429, 503}, first.text
    [row] = await ledger_rows(question.graph)
    assert row.status == "FINALIZED" and row.estimated_cost_usd == row.reserved_cost_usd
    assert Decimal("0.015") < row.reserved_cost_usd <= Decimal("0.02")
    assert await monthly_usage(question.graph, question.actor) == prior + row.reserved_cost_usd
    # The reservation stays counted, so the next question does not fit the limit any more.
    async with listening(handler) as port:
        second = await question.ask(knowledge_provider_for(kind, f"http://127.0.0.1:{port}/v1", timeout=timeout))
    assert second.status_code == 409 and second.json()["detail"]["code"] == "ORGANIZATION_AI_BUDGET_EXCEEDED"
    assert len(await ledger_rows(question.graph)) == 1


class RaisingProvider(FakeKnowledgeProvider):
    """The fake's reservation (17 input tokens) and a failure of the test's choosing instead of an answer."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    async def answer(self, request, *, max_output_tokens):
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize("error, booked", [
    (ProviderUnavailable(not_sent=True), "zero"),
    (ProviderUnavailable(), "reserved"),
    (ProviderTimeout(), "reserved"),
    (ProviderInvalidOutput(), "reserved"),
    (RuntimeError("an adapter error that names no phase"), "reserved"),
    # Reported usage always wins: a failure that carries usage is booked by it, whatever else it claims.
    (ProviderUnavailable(not_sent=True, usage=ProviderUsage(17, 11, Decimal("0.0000166"), 1)), "usage"),
], ids=["not sent", "unknown", "timeout", "invalid output", "unexpected", "usage excludes not sent"])
async def test_the_service_books_zero_only_for_a_request_that_never_left(error, booked, question):
    answer = await question.ask(RaisingProvider(error))
    assert answer.status_code in {200, 429, 503}, answer.text
    [row] = await ledger_rows(question.graph)
    expected = {"zero": Decimal("0"), "reserved": row.reserved_cost_usd, "usage": Decimal("0.000017")}[booked]
    assert (row.status, row.estimated_cost_usd) == ("FINALIZED", expected)
    if booked == "zero":
        assert (answer.status_code, answer.json()["detail"]) == (503, "QA_TEMPORARILY_UNAVAILABLE")
