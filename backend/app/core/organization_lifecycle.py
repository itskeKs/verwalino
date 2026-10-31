"""Translate only the immediate lifecycle guard conflict at the HTTP boundary."""
from fastapi import HTTPException, Request
from sqlalchemy.exc import DBAPIError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_id import http_error


class OrganizationLifecycleMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        started = False

        async def track_start(message: Message) -> None:
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track_start)
        except DBAPIError as exc:
            # Registering DBAPIError as a handled exception would make Starlette
            # wrap unrelated deferred commit failures after response start.
            if started or getattr(exc.orig, 'sqlstate', None) != 'P0023':
                raise
            response = await http_error(Request(scope), HTTPException(409,
                'Ihre Organisation ist während der Löschung schreibgeschützt.'))
            await response(scope, receive, send)
