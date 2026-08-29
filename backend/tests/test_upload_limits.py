"""Tests for bounded multipart upload reads."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core import uploads
from app.core.uploads import (
    MAX_UPLOAD_BYTES,
    RequestBodyLimitMiddleware,
    read_upload_limited,
)


class ChunkedUpload:
    def __init__(self, content: bytes):
        self._content = content
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else self._offset + size
        chunk = self._content[self._offset : end]
        self._offset += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_read_upload_limited_accepts_exact_limit():
    assert await read_upload_limited(ChunkedUpload(b"abcd"), max_bytes=4) == b"abcd"


@pytest.mark.asyncio
async def test_read_upload_limited_rejects_one_byte_over_limit():
    with pytest.raises(HTTPException) as exc_info:
        await read_upload_limited(ChunkedUpload(b"abcde"), max_bytes=4)

    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_read_upload_limited_rejects_empty_file():
    with pytest.raises(HTTPException) as exc_info:
        await read_upload_limited(ChunkedUpload(b""), max_bytes=4)

    assert exc_info.value.status_code == 422


async def _run_asgi_app(
    middleware, *, headers, messages, path="/upload", method="POST"
):
    sent = []
    received = iter(messages)

    async def receive():
        return next(received)

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "method": method, "path": path, "headers": headers},
        receive,
        send,
    )
    return sent


@pytest.mark.asyncio
async def test_request_body_limit_rejects_oversized_content_length_before_app():
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=[(b"content-length", b"5")],
        messages=[],
        path="/api/invoice-documents/upload",
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
async def test_request_body_limit_rejects_body_exceeding_missing_or_forged_content_length(
    headers,
):
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                break

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=headers,
        messages=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ],
        path="/api/invoice-documents/upload",
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_request_body_limit_forwards_each_allowed_message_without_aggregation():
    messages = [
        {
            "type": "http.request",
            "body": b"a" * uploads.UPLOAD_CHUNK_BYTES,
            "more_body": True,
        },
        {"type": "http.request", "body": b"b", "more_body": False},
    ]
    received = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            received.append(message)
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(
            downstream, max_body_bytes=uploads.UPLOAD_CHUNK_BYTES + 1
        ),
        headers=[],
        messages=messages,
        path="/api/invoice-documents/upload",
    )

    assert b"".join(message["body"] for message in received) == (
        b"a" * uploads.UPLOAD_CHUNK_BYTES + b"b"
    )
    assert len(received) == 2
    assert len(received[0]["body"]) == uploads.UPLOAD_CHUNK_BYTES
    assert received[0]["more_body"] is True
    assert received[1]["body"] == b"b"
    assert received[1]["more_body"] is False
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_request_body_limit_translates_chunked_oversize_before_response_start():
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=[],
        messages=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ],
        path="/api/invoice-documents/upload",
    )

    assert called is False
    assert [
        message["status"]
        for message in sent
        if message["type"] == "http.response.start"
    ] == [413]


@pytest.mark.asyncio
async def test_request_body_limit_applies_to_non_multipart_requests():
    received = []

    async def downstream(scope, receive, send):
        received.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=[(b"content-type", b"application/json")],
        messages=[{"type": "http.request", "body": b"abcde", "more_body": False}],
        path="/api/not-an-upload",
    )

    assert sent[0]["status"] == 200
    assert received[0]["body"] == b"abcde"


@pytest.mark.asyncio
async def test_request_body_limit_closes_spool_after_upload_downstream_returns(monkeypatch):
    created = []
    original_spool = uploads.tempfile.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        spool = original_spool(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(uploads.tempfile, "SpooledTemporaryFile", tracked_spool)

    async def downstream(scope, receive, send):
        while (await receive()).get("more_body", False):
            pass

    await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=MAX_UPLOAD_BYTES),
        headers=[],
        messages=[{"type": "http.request", "body": b"ok", "more_body": False}],
        path="/api/invoice-documents/upload",
    )

    assert len(created) == 1
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_request_body_limit_passes_post_replay_receives_to_original_stream():
    messages = [
        {"type": "http.request", "body": b"ok", "more_body": False},
        {"type": "http.disconnect"},
    ]
    received = []

    async def downstream(scope, receive, send):
        received.append(await receive())
        received.append(await receive())

    await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=[],
        messages=messages,
        path="/api/invoice-documents/upload",
    )

    assert received[0] == {
        "type": "http.request",
        "body": b"ok",
        "more_body": False,
    }
    assert received[1] is messages[1]


@pytest.mark.asyncio
async def test_request_body_limit_upload_trailing_slash_is_prevalidated():
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=[],
        messages=[{"type": "http.request", "body": b"abcde", "more_body": False}],
        path="/api/mandates/mandate-id/documents/",
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/declaration-imports/import-id/documents",
        "/api/declaration-imports/import-id/documents/",
    ],
)
async def test_request_body_limit_rejects_declaration_upload_content_length_before_downstream(
    path,
):
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=[(b"content-length", b"5")],
        messages=[],
        path=path,
    )

    assert called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,headers",
    [
        ("/api/declaration-imports/import-id/documents", []),
        (
            "/api/declaration-imports/import-id/documents/",
            [(b"content-length", b"1")],
        ),
    ],
)
async def test_request_body_limit_rejects_chunked_declaration_upload_and_closes_spool(
    monkeypatch,
    path,
    headers,
):
    created = []
    original_spool = uploads.tempfile.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        spool = original_spool(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(uploads.tempfile, "SpooledTemporaryFile", tracked_spool)
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run_asgi_app(
        RequestBodyLimitMiddleware(downstream, max_body_bytes=4),
        headers=headers,
        messages=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"de", "more_body": False},
        ],
        path=path,
    )

    assert called is False
    assert sent[0]["status"] == 413
    assert len(created) == 1
    assert created[0].closed is True
