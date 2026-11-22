"""Whether a failed provider request can have reached the provider, per transport (issue #833).

A failure that happens while the name is resolved, in the endpoint guard, while the connection is made or while the
TLS handshake runs is ``not_sent``: no byte of the request was written. Every failure after that point (a close or
a reset on a request that was written, no answer in time, an HTTP status) is unknown and stays False. The three
transports are the real ones, over real local sockets; only the far end is a local stand-in, and no name is looked
up on a network.
"""
import asyncio
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from openai import APIConnectionError

from app.core.config import settings
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.provider import PROVIDER_CONNECTION_ERROR, PROVIDER_TIMEOUT, DocumentInput, InvoiceCategory, PageInput, ProviderFailure
from tests.local_provider_endpoints import (
    KINDS,
    MODELS,
    UNRESOLVABLE,
    Encoder,
    close_after_request,
    closed_port,
    listening,
    never_answer,
    not_tls,
    provider_for,
    resolve_without_network,
)

pytestmark = pytest.mark.no_db

DOCUMENT = DocumentInput("invoice", (PageInput("invoice", 1, "Mustermann Versicherungen AG\nGesamtbetrag 312,00 EUR", 1.0),))
CATEGORIES = (InvoiceCategory("GEBAEUDEVERSICHERUNG", "Gebäudeversicherung"),)


async def failure_of(kind: str, base_url: str, *, timeout: int | None = None) -> ProviderFailure:
    provider = provider_for(kind, base_url, timeout=timeout)
    try:
        with pytest.raises(ProviderFailure) as raised:
            await provider.extract_invoice([DOCUMENT], categories=CATEGORIES, model=MODELS[kind], max_output_tokens=2000)
        return raised.value
    finally:
        await provider._client.close()


@pytest.fixture(params=KINDS)
def kind(request):
    return request.param


# ---- before the request was written: not sent --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_refused_connection_is_not_sent(kind):
    failure = await failure_of(kind, f"http://127.0.0.1:{closed_port()}/v1")
    assert (failure.code, failure.not_sent) == (PROVIDER_CONNECTION_ERROR, True)


@pytest.mark.asyncio
async def test_a_failed_tls_handshake_is_not_sent(kind):
    async with listening(not_tls) as port:
        failure = await failure_of(kind, f"https://127.0.0.1:{port}/v1")
    assert (failure.code, failure.not_sent) == (PROVIDER_CONNECTION_ERROR, True)


@pytest.mark.asyncio
async def test_a_name_that_does_not_resolve_is_not_sent(kind, monkeypatch):
    resolve_without_network(monkeypatch, address=None)
    failure = await failure_of(kind, f"https://{UNRESOLVABLE}/v1")
    assert (failure.code, failure.not_sent) == (PROVIDER_CONNECTION_ERROR, True)


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
    assert (failure.code, failure.not_sent) == (PROVIDER_CONNECTION_ERROR, True)
    assert connections == []


# ---- from the moment the request was written: unknown --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_connection_closed_on_a_request_that_was_read_is_unknown(kind):
    async with listening(close_after_request) as port:
        failure = await failure_of(kind, f"http://127.0.0.1:{port}/v1")
    assert (failure.code, failure.not_sent) == (PROVIDER_CONNECTION_ERROR, False)
    assert failure.usage is None and failure.usage_complete is False


@pytest.mark.asyncio
async def test_no_answer_in_time_is_unknown(kind):
    async with listening(never_answer) as port:
        failure = await failure_of(kind, f"http://127.0.0.1:{port}/v1", timeout=1)
    assert (failure.code, failure.not_sent) == (PROVIDER_TIMEOUT, False)


@pytest.mark.asyncio
async def test_a_timeout_while_connecting_stays_unknown(kind, monkeypatch):
    # A timeout is never proof, wherever it happened: it stays unknown. The SDK speaks through httpx2, the wire
    # transports through httpx.
    def stalled(library):
        async def send(self, *args, **options):
            raise library.ConnectTimeout("stalled")
        return send

    monkeypatch.setattr(httpx.AsyncClient, "send", stalled(httpx))
    monkeypatch.setattr(httpx2.AsyncClient, "send", stalled(httpx2))
    failure = await failure_of(kind, f"http://127.0.0.1:{closed_port()}/v1")
    assert (failure.code, failure.not_sent) == (PROVIDER_TIMEOUT, False)


def sdk_failing_with(cause):
    """An OpenAI-SDK-shaped client whose request fails the way the SDK reports it: a connection error whose cause is the
    HTTP library's error (the SDK raises ``APIConnectionError(...) from error``, with httpx2 errors)."""
    async def parse(**options):
        error = APIConnectionError(request=httpx2.Request("POST", "https://unit.test/responses"))
        error.__cause__ = cause
        raise error

    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return OpenAiDeclarationProvider(client=client, primary_model=MODELS["openai"], escalation_model=MODELS["openai"], token_encoder=Encoder())


@pytest.mark.asyncio
@pytest.mark.parametrize("cause,not_sent", [
    (httpx2.ConnectError("refused"), True),
    (httpx.ConnectError("refused"), True),
    (httpx2.ReadError("reset"), False),
    (httpx2.WriteError("broken pipe"), False),
    (httpx2.RemoteProtocolError("disconnected"), False),
    (httpx2.ProxyError("tunnel"), False),
    (httpx2.ConnectTimeout("stalled"), False),
    (None, False),
], ids=lambda value: f"{type(value).__module__}.{type(value).__name__}" if isinstance(value, Exception) else str(value))
async def test_the_sdk_reports_the_phase_through_the_cause_of_its_connection_error(cause, not_sent):
    provider = sdk_failing_with(cause)
    with pytest.raises(ProviderFailure) as raised:
        await provider.extract_invoice([DOCUMENT], categories=CATEGORIES, model=MODELS["openai"], max_output_tokens=2000)
    assert (raised.value.code, raised.value.not_sent) == (PROVIDER_CONNECTION_ERROR, not_sent)
