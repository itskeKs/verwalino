"""Shared, bounded multipart upload helpers."""

from __future__ import annotations

import tempfile

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing import Protocol

from fastapi import HTTPException


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 64 * 1024
UPLOAD_SPOOL_MEMORY_BYTES = 64 * 1024
# Multipart boundaries and form fields add overhead to the file payload.  The
# raw ASGI request cap leaves a deliberately small 1 MiB envelope buffer while
# the reader below still enforces the exact 25 MiB file-content cap.
MULTIPART_ENVELOPE_BYTES = 1 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = MAX_UPLOAD_BYTES + MULTIPART_ENVELOPE_BYTES


class UploadReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class RequestBodyLimitMiddleware:
    """Prevalidate the raw body of the application's upload endpoints.

    The application-wide raw request limit is 26 MiB: 25 MiB file content and
    up to 1 MiB multipart envelope.  The reverse proxy must enforce the same
    global cap for non-upload requests; those requests pass through here
    unchanged.  Upload bodies are spooled with a bounded in-memory threshold,
    then replayed to the application in fixed-size ASGI messages.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int = MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_upload_scope(scope):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope.get("headers", []) if name.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._send_too_large(send)
                    return
            except ValueError:
                pass

        spool = tempfile.SpooledTemporaryFile(
            max_size=UPLOAD_SPOOL_MEMORY_BYTES, mode="w+b"
        )
        try:
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                body = message.get("body", b"")
                total += len(body)
                if total > self.max_body_bytes:
                    await self._send_too_large(send)
                    return
                spool.write(body)
                if not message.get("more_body", False):
                    break

            spool.seek(0)
            replay_finished = total == 0
            replay_empty_sent = False
            replayed_bytes = 0

            async def replay_receive() -> Message:
                nonlocal replay_finished, replay_empty_sent, replayed_bytes
                if not replay_finished:
                    if replayed_bytes < total:
                        body = spool.read(
                            min(UPLOAD_CHUNK_BYTES, total - replayed_bytes)
                        )
                        replayed_bytes += len(body)
                        replay_finished = replayed_bytes >= total
                        return {
                            "type": "http.request",
                            "body": body,
                            "more_body": not replay_finished,
                        }
                if not replay_empty_sent and total == 0:
                    replay_empty_sent = True
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                return await receive()

            await self.app(scope, replay_receive, send)
        finally:
            spool.close()

    @staticmethod
    def _is_upload_scope(scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method", "").upper() != "POST":
            return False
        parts = scope.get("path", "").rstrip("/").strip("/").split("/")
        if parts == ["api", "invoice-documents", "upload"]:
            return True
        return (
            len(parts) == 4
            and parts[0] == "api"
            and parts[2]
            and (
                parts[1] == "invoice-documents"
                and parts[3] == "version"
                or parts[1] == "mandates"
                and parts[3] == "documents"
            )
        )

    @staticmethod
    async def _send_too_large(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})


async def read_upload_limited(
    upload: UploadReader, *, max_bytes: int = MAX_UPLOAD_BYTES
) -> bytes:
    """Read an upload in fixed chunks and reject empty or oversized content."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(UPLOAD_CHUNK_BYTES, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="File too large (max 25 MB)")
        chunks.append(chunk)

    if total == 0:
        raise HTTPException(status_code=422, detail="Empty file")
    return b"".join(chunks)
