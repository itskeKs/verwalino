"""Expose the real dunning response before request cleanup in browser tests.

Hold POST cleanup until the first detail GET completes. This makes the observed
post-create race deterministic without replacing any request, response or data.
Function-scope transactions have already committed before this scheduling gate.
"""
import asyncio
import json

from app.main import app as real_app
from tests.bank_browser_trace import PATH, install_database_trace, trace_bank_request

pending_reads: dict[str, asyncio.Event] = {}
install_database_trace()


async def app(scope, receive, send):
    if scope['type'] != 'http':
        return await real_app(scope, receive, send)
    if scope['method'] == 'GET' and scope['path'] == PATH:
        return await trace_bank_request(real_app, scope, receive, send)
    create = scope['method'] == 'POST' and scope['path'] in {'/api/dunning/owner', '/api/dunning/tenant'}
    first_read = pending_reads.get(scope['path']) if scope['method'] == 'GET' else None
    successful = False
    body = bytearray()

    async def send_before_cleanup(message):
        nonlocal successful
        if message['type'] == 'http.response.start':
            successful = message['status'] == 201
        if create and successful and message['type'] == 'http.response.body':
            body.extend(message.get('body', b''))
            if not message.get('more_body', False):
                path = f"/api/dunning/{json.loads(body)['id']}"
                read = pending_reads[path] = asyncio.Event()
                try:
                    await send(message)
                    # A failed/abandoned navigation must still release the real
                    # transaction and let the owned server shut down normally.
                    await asyncio.wait_for(read.wait(), timeout=5)
                except TimeoutError:
                    pass
                finally:
                    pending_reads.pop(path, None)
                return
        await send(message)

    try:
        await real_app(scope, receive, send_before_cleanup)
    finally:
        if first_read is not None:
            first_read.set()
