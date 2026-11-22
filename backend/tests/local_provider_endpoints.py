"""Real local sockets and resolver stand-ins for the transports of a model provider (issue #833).

A request fails at one of two places: before the request is written (the name does not resolve, the endpoint guard
refuses the address, the connection is refused, the TLS handshake fails) or after (the server closes on a request
it has read, or never answers). Each of them is reproduced with a socket on 127.0.0.1 or a resolver that answers
without a network call, so that the three transports (the OpenAI SDK, the OpenAI-compatible and the Anthropic wire)
are exercised for real and only the far end is local.
"""
import asyncio
import socket
from contextlib import asynccontextmanager

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.provider_runtime import create_extraction_provider
from app.services.ai_settings_service import EffectiveAiSettings
from tests.test_invoice_extraction_provider import _Encoder as Encoder

KINDS = ("openai", "openai_compatible", "anthropic")
MODELS = {"openai": "gpt-5.6-luna", "openai_compatible": "local-text", "anthropic": "claude-haiku-4-5"}
UNRESOLVABLE = "provider.invalid"


def provider_for(kind: str, base_url: str, *, model: str | None = None, timeout: int | None = None):
    """The real extraction transport of ``kind`` aimed at ``base_url``; the OpenAI one over a real SDK client."""
    model = model or MODELS[kind]
    if kind == "openai":
        client = AsyncOpenAI(api_key="synthetic-key", base_url=base_url, max_retries=0)
        return OpenAiDeclarationProvider(client=client, primary_model=model, escalation_model=model, timeout_seconds=timeout,
                                         token_encoder=Encoder())
    return create_extraction_provider(EffectiveAiSettings(provider=kind, api_key=SecretStr("synthetic-key"), model=model,
                                                          base_url=base_url, request_timeout_seconds=timeout))


def closed_port() -> int:
    """A loopback port nobody listens on: a connection to it is refused."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@asynccontextmanager
async def listening(handler):
    """A local server on a free loopback port; ``handler(reader, writer)`` serves every connection. Yields the port."""
    writers = []

    async def serve(reader, writer):
        writers.append(writer)
        try:
            await handler(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # the client gave up first

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await server.wait_closed()


async def _read_request(reader) -> None:
    """Take in one request, headers and body: the far end has then really been sent it."""
    head = await reader.readuntil(b"\r\n\r\n")
    length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")), 0)
    await reader.readexactly(length)


async def close_after_request(reader, writer):
    """Reads the whole request and closes the connection without an answer."""
    await _read_request(reader)
    writer.close()


async def never_answer(reader, writer):
    """Reads the whole request and then says nothing until the client gives up and closes."""
    await _read_request(reader)
    await reader.read()


async def not_tls(reader, writer):
    """Answers a TLS ClientHello with plain text: the handshake of an https client cannot complete."""
    await reader.read(1)
    writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
    await writer.drain()
    writer.close()


def resolve_without_network(monkeypatch: pytest.MonkeyPatch, *, address: str | None) -> None:
    """``UNRESOLVABLE`` resolves to ``address`` (None: the name does not resolve) for the rest of the test; every other
    name resolves as usual. The transports resolve through ``socket.getaddrinfo``; nothing leaves the machine."""
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **options):
        if host == UNRESOLVABLE:
            if address is None:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
        return real(host, port, *args, **options)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
